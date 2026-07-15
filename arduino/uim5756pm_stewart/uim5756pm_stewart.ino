/*
  UIM5756PM 3-axis Stewart / tilt table controller

  Uses three independent AccelStepper objects for acceleration-profiled,
  non-blocking step generation. Coordinated multi-axis moves scale each axis's
  max speed AND acceleration by the same factor (steps_i / max_steps) so all
  axes accelerate, cruise, and decelerate in proportion and arrive together.

  Serial monitor: 115200 baud, newline enabled.

  CALIBRATION (required before motion):
    Manually move all three cranks so they point STRAIGHT UP (max heave),
    motors disabled / free to turn. Then send:
      calibrate
    That pose becomes the step + heave reference. Until calibrated, enable /
    pose / vel / angle / steps / jog are rejected.

  Main commands:
    calibrate              (alias: zero)  — cranks-up = max heave reference
    pose <roll_deg> <pitch_deg> <heave_mm>
    vel <roll_deg_s> <pitch_deg_s> <heave_mm_s>
    angle <a0_deg> <a1_deg> <a2_deg>
    steps <s0> <s1> <s2>
    jog <axis> <pulses>
    enable [axis]
    disable [axis]
    hold                   — enable all drivers and hold current positions
    status
    help
*/

#include <AccelStepper.h>
#include <Arduino.h>
#include <EEPROM.h>
#include <avr/wdt.h>
#include <math.h>
#include <ctype.h>
#include <stdlib.h>

const uint8_t AXES = 3;

// Capture reset cause before Arduino/libc initialization clears context.
// External reset (USB DTR / reset button) may restore a saved live position;
// power-on/brown-out/watchdog resets always require calibration.
uint8_t resetCause __attribute__((section(".noinit")));

// ------------------------- Wiring -------------------------
// Per-axis pin groups on the Uno R3 (2026-07-09 harness):
//   Axis 0: PLS=D2  DIR=D3  ENA=D4
//   Axis 1: PLS=D7  DIR=D8  ENA=D9
//   Axis 2: PLS=D11 DIR=D12 ENA=D13
// Amazon / UIM344 Fig 0-6 cable colors (no purple on these motors):
//   Brown=COM→5V, Gray=DIR, Yellow=PLS, Blue=ENA, Black(signal)=GND
//   White=TX / Green=RX leave unconnected (config only).
const uint8_t PLS_PIN[AXES] = {2, 7, 11};
const uint8_t DIR_PIN[AXES] = {3, 8, 12};
const uint8_t ENA_PIN[AXES] = {4, 9, 13};
const bool ENA_ACTIVE_LOW = true;

const bool DIR_INVERT[AXES] = {false, false, false};

const bool USE_LIMITS = false;
const uint8_t LIMIT_PIN[AXES] = {5, 6, 10};  // unused while USE_LIMITS is false
const bool LIMIT_ACTIVE_LOW = true;

// --------------------- Motion calibration ---------------------
// Pulses per crank revolution as configured on these motors (do not change
// motor MCS to match firmware — match firmware to the motors).
// MCS=8: 200 full steps/rev × 8 microsteps × 20:1 MGL23-G20-D8.
// All three motors MUST be configured to MCS=8 before running this firmware.
const float STEPS_PER_CRANK_REV[AXES] = {32000.0, 32000.0, 32000.0};

// Maximum crank speed in deg/s and acceleration in deg/s².
// Quieter profile (was 90 / 180): lower peak + softer ramp reduces
// gearbox/table noise while keeping direction changes responsive.
const float MAX_CRANK_SPEED_DEG_S = 25.0;
const float MAX_CRANK_ACCEL_DEG_S2 = 40.0;

// ----------------------- Platform geometry -----------------------
// 2026-07-09: motors moved inward. At max heave the cranks AND arms are
// vertical ⇒ motor shaft radius == platform rod radius (119 mm).
const float TABLE_ROD_RADIUS_MM = 119.0;
const float CRANK_RADIUS_MM = 30.0;
const float ARM_LENGTH_MM = 110.0;
const float BASE_MOTOR_RADIUS_MM = 119.0;
const float NEUTRAL_TOP_Z_MM = 110.0;
const float LEG_AZIMUTH_DEG[AXES] = {0.0, 120.0, 240.0};
const float ROD_END_LIMIT_DEG = 14.0;
const float SUPPORT_STROKE_MM = 20.66;
// Angle reference for step counters: 180° = crank horizontal-inward.
// With BASE==TABLE this is no longer a closed "arm vertical" pose; it is
// only the zero for crankDeltaToSteps / status axisN_deg reporting.
const float NEUTRAL_CRANK_DEG = 180.0;
// Calibration pose: crank STRAIGHT UP (sin=+1) = max heave. Human places
// all three cranks here with motors free, then sends `calibrate`.
const float CALIBRATE_CRANK_DEG = 90.0;
// Max-heave closed form with BASE==TABLE (radial gap 0, arm vertical):
//   platform_z = CRANK_RADIUS + ARM_LENGTH = 30 + 110 = 140
//   heave = platform_z - NEUTRAL_TOP_Z = 140 - 110 = 30
const float CALIBRATE_HEAVE_MM = 30.0;

const float MAX_ROLL_DEG = 5.0;
const float MAX_PITCH_DEG = 5.0;
// With BASE==TABLE, rod-end misalignment exceeds 14° below ~12 mm heave
// (level pose). Keep a small margin above that floor.
const float MIN_HEAVE_MM = 12.0;
// Allow commanding up to the calibrated max-heave pose.
const float MAX_HEAVE_MM = CALIBRATE_HEAVE_MM;
const unsigned long VELOCITY_UPDATE_MS = 25;
const float VELOCITY_EPSILON = 0.001;

struct Vec3 { float x, y, z; };

AccelStepper stepper[AXES] = {
  AccelStepper(AccelStepper::DRIVER, PLS_PIN[0], DIR_PIN[0]),
  AccelStepper(AccelStepper::DRIVER, PLS_PIN[1], DIR_PIN[1]),
  AccelStepper(AccelStepper::DRIVER, PLS_PIN[2], DIR_PIN[2]),
};

Vec3 motorShaft[AXES];
Vec3 topRodNeutral[AXES];
bool axisEnabled[AXES] = {false, false, false};
long desiredTarget[AXES] = {0, 0, 0};
bool calibrated = false;
// Interactive per-axis calibration (host TUI jogs each crank to vertical).
bool benchCalMode = false;
bool axisMarked[AXES] = {false, false, false};
float configuredMaxSpeed[AXES] = {-1.0, -1.0, -1.0};
float configuredAcceleration[AXES] = {-1.0, -1.0, -1.0};
bool restoredPosition = false;
bool persistentSnapshotValid = false;

const uint32_t PERSIST_MAGIC = 0x54545950UL;  // "TTYP"
const uint16_t PERSIST_VERSION = 1;
struct PersistedPosition {
  uint32_t magic;
  uint16_t version;
  int32_t steps[AXES];
  float rollDeg;
  float pitchDeg;
  float heaveMm;
  uint16_t checksum;
};

void captureResetCause(void) __attribute__((naked, section(".init3")));
void captureResetCause(void) {
  resetCause = MCUSR;
  MCUSR = 0;
  wdt_disable();
}

String line;
float currentRollDeg = 0.0;
float currentPitchDeg = 0.0;
float currentHeaveMm = 0.0;
float rollVelocityDegS = 0.0;
float pitchVelocityDegS = 0.0;
float heaveVelocityMmS = 0.0;
bool velocityMode = false;
unsigned long lastVelocityUpdateMs = 0;

// ----------------------- Helpers -----------------------

float degToRad(float deg) { return deg * PI / 180.0; }

float clampFloat(float v, float lo, float hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

uint8_t logicLevel(bool active, bool activeLow) {
  return activeLow ? (active ? LOW : HIGH) : (active ? HIGH : LOW);
}

float fullSpeedStepsPerSec(uint8_t i) {
  return MAX_CRANK_SPEED_DEG_S * STEPS_PER_CRANK_REV[i] / 360.0;
}

float fullAccelStepsPerSec2(uint8_t i) {
  return MAX_CRANK_ACCEL_DEG_S2 * STEPS_PER_CRANK_REV[i] / 360.0;
}

bool profileValueChanged(float current, float requested) {
  if (current < 0.0) return true;
  // AccelStepper recomputes a square root when acceleration changes. Ignore
  // tiny ratio noise from 30 Hz live pose retargeting while preserving
  // coordinated motion to within 2%.
  float tolerance = max(1.0, fabs(current) * 0.02);
  return fabs(current - requested) > tolerance;
}

void setMotionProfile(uint8_t i, float maxSpeed, float acceleration) {
  if (profileValueChanged(configuredMaxSpeed[i], maxSpeed)) {
    stepper[i].setMaxSpeed(maxSpeed);
    configuredMaxSpeed[i] = maxSpeed;
  }
  if (profileValueChanged(configuredAcceleration[i], acceleration)) {
    stepper[i].setAcceleration(acceleration);
    configuredAcceleration[i] = acceleration;
  }
}

uint16_t persistedChecksum(const PersistedPosition &state) {
  const uint8_t *bytes = reinterpret_cast<const uint8_t *>(&state);
  uint16_t value = 0x5A5A;
  for (size_t i = 0; i < sizeof(PersistedPosition) - sizeof(state.checksum); i++) {
    value = (uint16_t)((value << 5) | (value >> 11));
    value ^= bytes[i];
  }
  return value;
}

bool validPersistedPosition(PersistedPosition &state) {
  if (state.magic != PERSIST_MAGIC || state.version != PERSIST_VERSION) return false;
  if (state.checksum != persistedChecksum(state)) return false;
  if (!isfinite(state.rollDeg) || !isfinite(state.pitchDeg) || !isfinite(state.heaveMm)) return false;
  if (fabs(state.rollDeg) > 20.0 || fabs(state.pitchDeg) > 20.0) return false;
  if (state.heaveMm < MIN_HEAVE_MM || state.heaveMm > MAX_HEAVE_MM) return false;
  for (uint8_t i = 0; i < AXES; i++) {
    if (labs(state.steps[i]) > 10000000L) return false;
  }
  return true;
}

void clearPersistedPosition() {
  if (!persistentSnapshotValid) return;
  PersistedPosition empty = {};
  EEPROM.put(0, empty);
  restoredPosition = false;
  persistentSnapshotValid = false;
}

bool savePersistentPosition() {
  if (!calibrated || benchCalMode || moving()) return false;
  PersistedPosition state = {};
  state.magic = PERSIST_MAGIC;
  state.version = PERSIST_VERSION;
  for (uint8_t i = 0; i < AXES; i++) {
    state.steps[i] = stepper[i].currentPosition();
  }
  state.rollDeg = currentRollDeg;
  state.pitchDeg = currentPitchDeg;
  state.heaveMm = currentHeaveMm;
  state.checksum = persistedChecksum(state);
  EEPROM.put(0, state);
  persistentSnapshotValid = true;
  return true;
}

bool loadPersistentPosition() {
  PersistedPosition state;
  EEPROM.get(0, state);
  if (!validPersistedPosition(state)) return false;
  persistentSnapshotValid = true;

  // Never trust saved motor coordinates after a controller power-cycle.
  bool externalReset = (resetCause & _BV(EXTRF)) != 0;
  bool unsafeReset = (resetCause & (_BV(PORF) | _BV(BORF) | _BV(WDRF))) != 0;
  if (!externalReset || unsafeReset) return false;

  for (uint8_t i = 0; i < AXES; i++) {
    stepper[i].setCurrentPosition(state.steps[i]);
    desiredTarget[i] = state.steps[i];
    axisMarked[i] = true;
  }
  currentRollDeg = state.rollDeg;
  currentPitchDeg = state.pitchDeg;
  currentHeaveMm = state.heaveMm;
  benchCalMode = false;
  calibrated = true;
  restoredPosition = true;
  persistentSnapshotValid = true;
  return true;
}

bool anyAxisEnabled() {
  for (uint8_t i = 0; i < AXES; i++) if (axisEnabled[i]) return true;
  return false;
}

void setAxisEnable(uint8_t i, bool on) {
  axisEnabled[i] = on;
  digitalWrite(ENA_PIN[i], logicLevel(on, ENA_ACTIVE_LOW));
}

void setEnable(bool on) {
  for (uint8_t i = 0; i < AXES; i++) setAxisEnable(i, on);
}

void holdCurrentPosition() {
  stopVelocityMode();
  for (uint8_t i = 0; i < AXES; i++) {
    desiredTarget[i] = stepper[i].currentPosition();
    stepper[i].moveTo(desiredTarget[i]);
  }
  setEnable(true);
}

bool limitHit(uint8_t i) {
  if (!USE_LIMITS) return false;
  int s = digitalRead(LIMIT_PIN[i]);
  return LIMIT_ACTIVE_LOW ? (s == LOW) : (s == HIGH);
}

// ----------------------- Geometry -----------------------

void buildGeometry() {
  for (uint8_t i = 0; i < AXES; i++) {
    float a = degToRad(LEG_AZIMUTH_DEG[i]);
    motorShaft[i]    = {BASE_MOTOR_RADIUS_MM * cos(a), BASE_MOTOR_RADIUS_MM * sin(a), 0.0};
    topRodNeutral[i] = {TABLE_ROD_RADIUS_MM  * cos(a), TABLE_ROD_RADIUS_MM  * sin(a), 0.0};
  }
}

Vec3 rotateRollPitch(Vec3 p, float rollRad, float pitchRad) {
  float cr = cos(rollRad), sr = sin(rollRad);
  float cp = cos(pitchRad), sp = sin(pitchRad);
  float x1 = p.x, y1 = p.y * cr - p.z * sr, z1 = p.y * sr + p.z * cr;
  return {x1 * cp + z1 * sp, y1, -x1 * sp + z1 * cp};
}

Vec3 topRodPosition(uint8_t i, float rollDeg, float pitchDeg, float heaveMm) {
  Vec3 p = rotateRollPitch(topRodNeutral[i], degToRad(rollDeg), degToRad(pitchDeg));
  p.z += NEUTRAL_TOP_Z_MM + heaveMm;
  return p;
}

float normalizeDeg(float deg) {
  while (deg >  180.0) deg -= 360.0;
  while (deg < -180.0) deg += 360.0;
  return deg;
}

long crankDeltaToSteps(uint8_t i, float crankDeltaDeg) {
  return lround(crankDeltaDeg * STEPS_PER_CRANK_REV[i] / 360.0);
}

float stepsToCrankDelta(uint8_t i, long steps) {
  return steps * 360.0 / STEPS_PER_CRANK_REV[i];
}

Vec3 crankPinPosition(uint8_t i, float crankDeg) {
  float la = degToRad(LEG_AZIMUTH_DEG[i]), cr = degToRad(crankDeg);
  return {
    motorShaft[i].x + CRANK_RADIUS_MM * cos(cr) * cos(la),
    motorShaft[i].y + CRANK_RADIUS_MM * cos(cr) * sin(la),
    CRANK_RADIUS_MM * sin(cr)
  };
}

float rodEndMisalignmentDeg(Vec3 top, Vec3 pin) {
  float dx = top.x - pin.x, dy = top.y - pin.y, dz = top.z - pin.z;
  return atan2(sqrt(dx*dx + dy*dy), dz) * 180.0 / PI;
}

bool solveCrankAngle(uint8_t i, Vec3 top, float *crankDeg, float *misalignDeg) {
  float la = degToRad(LEG_AZIMUTH_DEG[i]);
  float ux = cos(la), uy = sin(la), vx = -sin(la), vy = cos(la);

  float topR = top.x * ux + top.y * uy;
  float topT = top.x * vx + top.y * vy;
  float armSq = ARM_LENGTH_MM * ARM_LENGTH_MM;
  if (topT * topT > armSq) return false;

  float effArm = sqrt(armSq - topT * topT);
  float a = topR - BASE_MOTOR_RADIUS_MM, b = top.z;
  float dist = sqrt(a * a + b * b);
  if (dist < 0.001) return false;

  float cosTerm = (dist*dist + CRANK_RADIUS_MM*CRANK_RADIUS_MM - effArm*effArm)
                  / (2.0 * CRANK_RADIUS_MM * dist);
  cosTerm = clampFloat(cosTerm, -1.0, 1.0);

  float phi = atan2(b, a), alpha = acos(cosTerm);
  float c0 = (phi + alpha) * 180.0 / PI;
  float c1 = (phi - alpha) * 180.0 / PI;
  *crankDeg = fabs(normalizeDeg(c0 - NEUTRAL_CRANK_DEG)) <= fabs(normalizeDeg(c1 - NEUTRAL_CRANK_DEG)) ? c0 : c1;
  *misalignDeg = rodEndMisalignmentDeg(top, crankPinPosition(i, *crankDeg));
  return true;
}

// ----------------------- Motion -----------------------

void applyCoordinatedTargets(long target[AXES]) {
  long maxDelta = 0;
  for (uint8_t i = 0; i < AXES; i++) {
    if (!axisEnabled[i]) continue;
    long d = abs(target[i] - stepper[i].currentPosition());
    if (d > maxDelta) maxDelta = d;
  }

  for (uint8_t i = 0; i < AXES; i++) {
    desiredTarget[i] = target[i];
    if (!axisEnabled[i]) continue;
    if (limitHit(i) && target[i] < stepper[i].currentPosition()) continue;

    float scale = (maxDelta > 0)
                  ? (float)abs(target[i] - stepper[i].currentPosition()) / (float)maxDelta
                  : 1.0;
    if (scale < 0.001) scale = 0.001;
    setMotionProfile(
      i,
      fullSpeedStepsPerSec(i) * scale,
      fullAccelStepsPerSec2(i) * scale
    );
    stepper[i].moveTo(target[i]);
  }
}

void moveToTargets() { applyCoordinatedTargets(desiredTarget); }

void setTargets(long t0, long t1, long t2) {
  long t[AXES] = {t0, t1, t2};
  applyCoordinatedTargets(t);
}

void moveAxisToTarget(uint8_t ax) {
  long t[AXES];
  for (uint8_t i = 0; i < AXES; i++) t[i] = stepper[i].currentPosition();
  t[ax] = desiredTarget[ax];
  applyCoordinatedTargets(t);
}

bool moving() {
  for (uint8_t i = 0; i < AXES; i++)
    if (axisEnabled[i] && stepper[i].distanceToGo() != 0) return true;
  return false;
}

bool jogAxis(uint8_t i, long pulses) {
  if (!axisEnabled[i]) { Serial.println(F("ERR enable axis first")); return false; }
  if (pulses < 0 && limitHit(i)) { Serial.println(F("ERR limit hit")); return false; }
  clearPersistedPosition();
  desiredTarget[i] = stepper[i].currentPosition() + pulses;
  setMotionProfile(i, fullSpeedStepsPerSec(i), fullAccelStepsPerSec2(i));
  stepper[i].move(pulses);
  return true;
}

// ----------------------- Pose / velocity -----------------------

bool solvePoseTargets(float roll, float pitch, float heave, long target[AXES]) {
  for (uint8_t i = 0; i < AXES; i++) {
    Vec3 top = topRodPosition(i, roll, pitch, heave);
    float crankDeg, misalignDeg;
    if (!solveCrankAngle(i, top, &crankDeg, &misalignDeg)) {
      Serial.println(F("ERR pose unreachable")); return false;
    }
    if (misalignDeg > ROD_END_LIMIT_DEG) {
      Serial.println(F("ERR pose exceeds rod-end angle")); return false;
    }
    target[i] = crankDeltaToSteps(i, normalizeDeg(crankDeg - NEUTRAL_CRANK_DEG));
  }
  return true;
}

bool moveToPose(float roll, float pitch, float heave) {
  long target[AXES];
  if (!solvePoseTargets(roll, pitch, heave, target)) return false;
  clearPersistedPosition();
  setTargets(target[0], target[1], target[2]);
  currentRollDeg = roll; currentPitchDeg = pitch; currentHeaveMm = heave;
  return true;
}

bool setPoseTarget(float roll, float pitch, float heave) {
  long target[AXES];
  if (!solvePoseTargets(roll, pitch, heave, target)) return false;
  clearPersistedPosition();
  applyCoordinatedTargets(target);
  currentRollDeg = roll; currentPitchDeg = pitch; currentHeaveMm = heave;
  return true;
}

void stopVelocityMode() {
  rollVelocityDegS = pitchVelocityDegS = heaveVelocityMmS = 0.0;
  velocityMode = false;
  for (uint8_t i = 0; i < AXES; i++) {
    desiredTarget[i] = stepper[i].currentPosition();
    stepper[i].moveTo(stepper[i].currentPosition());
  }
}

void zeroPosition() {
  // Deprecated name — same as calibratePosition().
  calibratePosition();
}

long calibrateStepCount(uint8_t i) {
  return crankDeltaToSteps(i, normalizeDeg(CALIBRATE_CRANK_DEG - NEUTRAL_CRANK_DEG));
}

void applyCalibratedPlatformState() {
  currentRollDeg = 0.0;
  currentPitchDeg = 0.0;
  currentHeaveMm = CALIBRATE_HEAVE_MM;
}

void calibratePosition() {
  // Legacy: human has manually placed all cranks STRAIGHT UP, then one-shot calibrate.
  stopVelocityMode();
  setEnable(false);
  benchCalMode = false;
  long calibSteps = calibrateStepCount(0);
  for (uint8_t i = 0; i < AXES; i++) {
    long s = calibrateStepCount(i);
    stepper[i].setCurrentPosition(s);
    desiredTarget[i] = s;
    axisMarked[i] = true;
  }
  applyCalibratedPlatformState();
  calibrated = true;
  restoredPosition = false;
  savePersistentPosition();
  Serial.print(F("OK calibrate heave "));
  Serial.print(CALIBRATE_HEAVE_MM, 3);
  Serial.print(F(" crank_deg "));
  Serial.print(CALIBRATE_CRANK_DEG, 1);
  Serial.print(F(" steps "));
  Serial.println(calibSteps);
}

void calBegin() {
  stopVelocityMode();
  setEnable(false);
  clearPersistedPosition();
  calibrated = false;
  benchCalMode = true;
  for (uint8_t i = 0; i < AXES; i++) {
    axisMarked[i] = false;
    stepper[i].setCurrentPosition(0);
    desiredTarget[i] = 0;
  }
  Serial.println(F("OK cal_begin — enable one axis, jog to vertical, cal_axis N, cal_finish"));
}

void calibrateAxis(uint8_t i) {
  if (!benchCalMode) {
    Serial.println(F("ERR cal_axis — send cal_begin first"));
    return;
  }
  stopVelocityMode();
  setAxisEnable(i, false);
  long calibSteps = calibrateStepCount(i);
  stepper[i].setCurrentPosition(calibSteps);
  desiredTarget[i] = calibSteps;
  axisMarked[i] = true;
  Serial.print(F("OK cal_axis "));
  Serial.print(i);
  Serial.print(F(" steps "));
  Serial.print(calibSteps);
  Serial.print(F(" deg "));
  Serial.println(stepsToCrankDelta(i, calibSteps), 3);
}

void calFinish() {
  if (!benchCalMode) {
    Serial.println(F("ERR cal_finish — send cal_begin first"));
    return;
  }
  for (uint8_t i = 0; i < AXES; i++) {
    if (!axisMarked[i]) {
      Serial.print(F("ERR cal_finish — axis "));
      Serial.print(i);
      Serial.println(F(" not marked; use cal_axis after jogging vertical"));
      return;
    }
  }
  stopVelocityMode();
  setEnable(false);
  benchCalMode = false;
  applyCalibratedPlatformState();
  calibrated = true;
  restoredPosition = false;
  savePersistentPosition();
  Serial.print(F("OK cal_finish heave "));
  Serial.print(CALIBRATE_HEAVE_MM, 3);
  Serial.print(F(" crank_deg "));
  Serial.println(CALIBRATE_CRANK_DEG, 1);
}

bool motionAllowedWithoutFullCal() {
  return calibrated || benchCalMode;
}

bool requireCalibrated() {
  if (calibrated) return true;
  Serial.println(F("ERR not calibrated — run cal_begin + cal_axis per leg, or legacy calibrate"));
  return false;
}

bool requireMotionAllowed() {
  if (motionAllowedWithoutFullCal()) return true;
  Serial.println(F("ERR not calibrated — send cal_begin for interactive setup, or calibrate"));
  return false;
}

void updateVelocityMotion() {
  if (!velocityMode || !anyAxisEnabled()) {
    lastVelocityUpdateMs = millis();
    return;
  }
  unsigned long nowMs = millis();
  unsigned long elapsedMs = nowMs - lastVelocityUpdateMs;
  if (elapsedMs < VELOCITY_UPDATE_MS) return;
  lastVelocityUpdateMs = nowMs;

  float dt = elapsedMs / 1000.0;
  float nextRoll  = clampFloat(currentRollDeg  + rollVelocityDegS  * dt, -MAX_ROLL_DEG,  MAX_ROLL_DEG);
  float nextPitch = clampFloat(currentPitchDeg + pitchVelocityDegS * dt, -MAX_PITCH_DEG, MAX_PITCH_DEG);
  float nextHeave = clampFloat(currentHeaveMm  + heaveVelocityMmS  * dt, MIN_HEAVE_MM,   MAX_HEAVE_MM);

  if (nextRoll == currentRollDeg && nextPitch == currentPitchDeg && nextHeave == currentHeaveMm) {
    stopVelocityMode();
    Serial.println(F("OK vel stopped at limit"));
    return;
  }
  if (!setPoseTarget(nextRoll, nextPitch, nextHeave)) {
    stopVelocityMode();
    Serial.println(F("ERR vel pose unreachable"));
  }
}

// ----------------------- Status / help -----------------------

void printStatus() {
  Serial.print(F("OK calibrated ")); Serial.print(calibrated ? F("1") : F("0"));
  Serial.print(F(" restored ")); Serial.print(restoredPosition ? F("1") : F("0"));
  Serial.print(F(" bench ")); Serial.print(benchCalMode ? F("1") : F("0"));
  Serial.print(F(" enabled ")); Serial.print(anyAxisEnabled() ? F("1") : F("0"));
  Serial.print(F(" moving "));   Serial.print(moving() ? F("1") : F("0"));
  for (uint8_t i = 0; i < AXES; i++) {
    Serial.print(F(" axis")); Serial.print(i); Serial.print(F("_marked ")); Serial.print(axisMarked[i] ? F("1") : F("0"));
    Serial.print(F(" axis")); Serial.print(i); Serial.print(F("_enabled ")); Serial.print(axisEnabled[i] ? F("1") : F("0"));
    Serial.print(F(" axis")); Serial.print(i); Serial.print(F("_steps "));   Serial.print(stepper[i].currentPosition());
    Serial.print(F(" axis")); Serial.print(i); Serial.print(F("_target "));  Serial.print(desiredTarget[i]);
    Serial.print(F(" axis")); Serial.print(i); Serial.print(F("_deg "));     Serial.print(stepsToCrankDelta(i, stepper[i].currentPosition()), 3);
    Serial.print(F(" axis")); Serial.print(i); Serial.print(F("_target_deg ")); Serial.print(stepsToCrankDelta(i, desiredTarget[i]), 3);
  }
  Serial.print(F(" roll "));      Serial.print(currentRollDeg, 3);
  Serial.print(F(" pitch "));     Serial.print(currentPitchDeg, 3);
  Serial.print(F(" heave "));     Serial.print(currentHeaveMm, 3);
  Serial.print(F(" vel_roll "));  Serial.print(rollVelocityDegS, 3);
  Serial.print(F(" vel_pitch ")); Serial.print(pitchVelocityDegS, 3);
  Serial.print(F(" vel_heave ")); Serial.println(heaveVelocityMmS, 3);
}

void printHelp() {
  Serial.println(F("UIM5756PM 3-axis Stewart controller"));
  Serial.println(F("Interactive calibration (jog each crank to vertical by eye):"));
  Serial.println(F("  cal_begin | cal_axis <0-2> | cal_finish"));
  Serial.println(F("Legacy: all cranks manually straight up, then calibrate (alias: zero)"));
  Serial.println(F("Commands (pose/vel need full calibrate; enable/jog OK in cal_begin):"));
  Serial.println(F("  pose <roll_deg> <pitch_deg> <heave_mm>"));
  Serial.println(F("  vel <roll_deg_s> <pitch_deg_s> <heave_mm_s>"));
  Serial.println(F("  angle <a0_deg> <a1_deg> <a2_deg>"));
  Serial.println(F("  steps <s0> <s1> <s2>"));
  Serial.println(F("  jog <axis> <pulses>"));
  Serial.println(F("  enable [axis] | disable [axis]"));
  Serial.println(F("  hold   (enable all drivers, hold current positions)"));
  Serial.println(F("  persist   (save settled calibrated position for USB-reset restore)"));
  Serial.println(F("  forget    (disable and require calibration; use before motor power-cycle)"));
  Serial.println(F("  status"));
  Serial.println(F("  help"));
}

// ----------------------- Command parsing -----------------------

int splitTokens(char *input, char *tokens[], int maxTokens) {
  int count = 0;
  char *token = strtok(input, " \t\r\n,");
  while (token && count < maxTokens) { tokens[count++] = token; token = strtok(NULL, " \t\r\n,"); }
  return count;
}

bool parsePose(char *tokens[], int count) {
  if (!requireCalibrated()) return false;
  if (count != 4) { Serial.println(F("ERR pose needs roll pitch heave")); return false; }
  stopVelocityMode();
  if (!moveToPose(atof(tokens[1]), atof(tokens[2]), atof(tokens[3]))) return false;
  Serial.print(F("OK pose targets"));
  for (uint8_t i = 0; i < AXES; i++) {
    Serial.print(F(" axis")); Serial.print(i); Serial.print(F("_steps ")); Serial.print(desiredTarget[i]);
    Serial.print(F(" axis")); Serial.print(i); Serial.print(F("_deg ")); Serial.print(stepsToCrankDelta(i, desiredTarget[i]), 3);
  }
  Serial.println();
  return true;
}

bool parseVelocity(char *tokens[], int count) {
  if (!requireCalibrated()) return false;
  if (count != 4) { Serial.println(F("ERR vel needs roll_deg_s pitch_deg_s heave_mm_s")); return false; }
  rollVelocityDegS  = atof(tokens[1]);
  pitchVelocityDegS = atof(tokens[2]);
  heaveVelocityMmS  = atof(tokens[3]);
  velocityMode = fabs(rollVelocityDegS) > VELOCITY_EPSILON ||
                 fabs(pitchVelocityDegS) > VELOCITY_EPSILON ||
                 fabs(heaveVelocityMmS)  > VELOCITY_EPSILON;
  lastVelocityUpdateMs = millis();
  Serial.print(F("OK vel roll ")); Serial.print(rollVelocityDegS, 3);
  Serial.print(F(" pitch "));      Serial.print(pitchVelocityDegS, 3);
  Serial.print(F(" heave "));      Serial.println(heaveVelocityMmS, 3);
  return true;
}

bool parseAngles(char *tokens[], int count) {
  if (!requireCalibrated()) return false;
  if (count != 4) { Serial.println(F("ERR angle needs three crank deltas in deg")); return false; }
  stopVelocityMode();
  long target[AXES];
  for (uint8_t i = 0; i < AXES; i++) target[i] = crankDeltaToSteps(i, atof(tokens[i + 1]));
  clearPersistedPosition();
  setTargets(target[0], target[1], target[2]);
  Serial.print(F("OK angle targets"));
  for (uint8_t i = 0; i < AXES; i++) {
    Serial.print(F(" axis")); Serial.print(i); Serial.print(F("_steps ")); Serial.print(desiredTarget[i]);
    Serial.print(F(" axis")); Serial.print(i); Serial.print(F("_deg ")); Serial.print(stepsToCrankDelta(i, desiredTarget[i]), 3);
  }
  Serial.println();
  return true;
}

bool parseSteps(char *tokens[], int count) {
  if (!requireCalibrated()) return false;
  if (count != 4) { Serial.println(F("ERR steps needs three step targets")); return false; }
  stopVelocityMode();
  clearPersistedPosition();
  setTargets(atol(tokens[1]), atol(tokens[2]), atol(tokens[3]));
  Serial.print(F("OK steps targets"));
  for (uint8_t i = 0; i < AXES; i++) {
    Serial.print(F(" axis")); Serial.print(i); Serial.print(F("_steps ")); Serial.print(desiredTarget[i]);
  }
  Serial.println();
  return true;
}

bool parseJog(char *tokens[], int count) {
  if (!requireMotionAllowed()) return false;
  if (count != 3) { Serial.println(F("ERR jog needs axis pulses")); return false; }
  long axisIndex = atol(tokens[1]);
  if (axisIndex < 0 || axisIndex >= AXES) { Serial.println(F("ERR jog axis must be 0, 1, or 2")); return false; }
  stopVelocityMode();
  uint8_t i = (uint8_t)axisIndex;
  if (!jogAxis(i, atol(tokens[2]))) return false;
  Serial.print(F("OK jog axis")); Serial.print(i);
  Serial.print(F("_steps ")); Serial.print(stepper[i].currentPosition());
  Serial.print(F(" axis")); Serial.print(i);
  Serial.print(F("_deg ")); Serial.print(stepsToCrankDelta(i, stepper[i].currentPosition()), 3);
  Serial.println();
  return true;
}

bool parseAxisIndex(char *token, uint8_t *out) {
  long v = atol(token);
  if (v < 0 || v >= AXES) { Serial.println(F("ERR axis must be 0, 1, or 2")); return false; }
  *out = (uint8_t)v;
  return true;
}

bool parseEnableCommand(char *tokens[], int count, bool on) {
  if (on && !requireMotionAllowed()) return false;
  if (count == 1) {
    setEnable(on);
    if (!on) stopVelocityMode();
    if (on) moveToTargets();
    Serial.println(on ? F("OK enable") : F("OK disable"));
    return true;
  }
  if (count != 2) { Serial.println(on ? F("ERR enable needs optional axis") : F("ERR disable needs optional axis")); return false; }
  uint8_t i;
  if (!parseAxisIndex(tokens[1], &i)) return false;
  setAxisEnable(i, on);
  if (!on) stopVelocityMode();
  if (on) moveAxisToTarget(i);
  Serial.print(on ? F("OK enable axis") : F("OK disable axis")); Serial.println(i);
  return true;
}

void handleCommand(String command) {
  command.trim();
  if (command.length() == 0) return;
  char buffer[96];
  command.toCharArray(buffer, sizeof(buffer));
  char *tokens[5];
  int count = splitTokens(buffer, tokens, 5);
  if (count == 0) return;

  String cmd = tokens[0]; cmd.toLowerCase();
  if      (cmd == "calibrate" || cmd == "calib" || cmd == "zero") calibratePosition();
  else if (cmd == "cal_begin" || cmd == "calbegin") {
    if (count != 1) Serial.println(F("ERR cal_begin takes no arguments"));
    else calBegin();
  }
  else if (cmd == "cal_axis" || cmd == "calaxis") {
    if (count != 2) { Serial.println(F("ERR cal_axis needs axis 0/1/2")); return; }
    uint8_t i;
    if (!parseAxisIndex(tokens[1], &i)) return;
    calibrateAxis(i);
  }
  else if (cmd == "cal_finish" || cmd == "calfinish") {
    if (count != 1) Serial.println(F("ERR cal_finish takes no arguments"));
    else calFinish();
  }
  else if (cmd == "pose"  || cmd == "p") parsePose(tokens, count);
  else if (cmd == "vel"   || cmd == "v") parseVelocity(tokens, count);
  else if (cmd == "angle" || cmd == "a") parseAngles(tokens, count);
  else if (cmd == "steps" || cmd == "s") parseSteps(tokens, count);
  else if (cmd == "jog"   || cmd == "j") parseJog(tokens, count);
  else if (cmd == "enable"  || cmd == "on")  parseEnableCommand(tokens, count, true);
  else if (cmd == "disable" || cmd == "off") parseEnableCommand(tokens, count, false);
  else if (cmd == "hold" || cmd == "lock") {
    if (count != 1) Serial.println(F("ERR hold takes no arguments"));
    else {
      holdCurrentPosition();
      Serial.println(F("OK holding current positions"));
    }
  }
  else if (cmd == "persist") {
    if (count != 1) Serial.println(F("ERR persist takes no arguments"));
    else if (savePersistentPosition()) {
      restoredPosition = false;
      Serial.println(F("OK persist saved"));
    } else {
      Serial.println(F("ERR persist requires calibrated, settled motion"));
    }
  }
  else if (cmd == "forget") {
    if (count != 1) Serial.println(F("ERR forget takes no arguments"));
    else {
      stopVelocityMode();
      setEnable(false);
      clearPersistedPosition();
      calibrated = false;
      benchCalMode = false;
      Serial.println(F("OK forget; calibration required"));
    }
  }
  else if (cmd == "status")              printStatus();
  else if (cmd == "help" || cmd == "?")  printHelp();
  else Serial.println(F("ERR unknown command"));
}

void readSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (line.length() > 0) { handleCommand(line); line = ""; }
    } else if (line.length() < 95) {
      line += c;
    }
  }
}

// ----------------------- Arduino lifecycle -----------------------

void setup() {
  Serial.begin(115200);

  for (uint8_t i = 0; i < AXES; i++) {
    // Active-low wiring: COM→5V, Arduino pins sink current.
    // Invert STEP so idle is HIGH (inactive). DIR_INVERT[i] flips direction per axis.
    stepper[i].setPinsInverted(DIR_INVERT[i], true, false);
    // UIM344 / UIM5756PM inputs need pulse width > 4 µs (manual).
    // Use 5 µs: above the documented minimum without wasting AVR step time.
    stepper[i].setMinPulseWidth(5);
    setMotionProfile(i, fullSpeedStepsPerSec(i), fullAccelStepsPerSec2(i));

    // Unknown physical pose until the human runs `calibrate`.
    // Leave step counters at 0; do not pretend we are at a known crank angle.
    stepper[i].setCurrentPosition(0);
    desiredTarget[i] = 0;

    pinMode(ENA_PIN[i], OUTPUT);
    if (USE_LIMITS) pinMode(LIMIT_PIN[i], LIMIT_ACTIVE_LOW ? INPUT_PULLUP : INPUT);
  }

  setEnable(false);
  calibrated = false;
  buildGeometry();
  bool restored = loadPersistentPosition();
  if (restored) {
    Serial.println(F("UIM5756PM Stewart ready. Position restored after external reset; motors disabled."));
  } else {
    Serial.println(F("UIM5756PM Stewart ready. Calibration required (power/reset cause not restorable)."));
  }
  Serial.println(F("Send 'help' for commands."));
}

void loop() {
  readSerial();
  updateVelocityMotion();
  for (uint8_t i = 0; i < AXES; i++) {
    if (axisEnabled[i]) stepper[i].run();
  }
}
