/*
  UIM5756PM 3-axis Stewart / tilt table controller

  This version drives PLS/DIR inputs directly. Targeted multi-axis moves are
  blocking but pulse all enabled axes together.

  Serial monitor: 115200 baud, newline enabled.

  Main commands:
    pose <roll_deg> <pitch_deg> <heave_mm>
    vel <roll_deg_s> <pitch_deg_s> <heave_mm_s>
    angle <a0_deg> <a1_deg> <a2_deg>
    steps <s0> <s1> <s2>
    jog <axis> <pulses>
    zero
    enable [axis]
    disable [axis]
    status
    help
*/

#include <Arduino.h>
#include <math.h>
#include <ctype.h>
#include <stdlib.h>

const uint8_t AXES = 3;

// ------------------------- Wiring -------------------------
// Change these to match your Arduino wiring. These Arduino pins connect to
// the signal-cable PLS, DIR, and ENA wires listed in the README.
const uint8_t PLS_PIN[AXES] = {2, 7, 11};
const uint8_t DIR_PIN[AXES] = {3, 8, 12};

// Your wiring diagram shows opto-isolated enable inputs with a COM wire. The
// default wiring in the README ties COM to Arduino 5V, so ENA is active LOW.
const uint8_t ENA_PIN[AXES] = {4, 9, 13};
const bool ENA_ACTIVE_LOW = true;

// If an axis moves the wrong way, flip its corresponding direction bit.
const bool DIR_INVERT[AXES] = {false, false, false};

// Optional normally-open home/limit switches. Set USE_LIMITS false if unused.
const bool USE_LIMITS = false;
const uint8_t LIMIT_PIN[AXES] = {9, 10, 11};
const bool LIMIT_ACTIVE_LOW = true;

// --------------------- Motion calibration ---------------------
// Set this from your drive/controller pulse setting.
const float STEPS_PER_CRANK_REV[AXES] = {32000.0, 32000.0, 32000.0};

const float MAX_CRANK_SPEED_DEG_S = 90.0;
const unsigned int STEP_PULSE_WIDTH_US = 3;

// ----------------------- Platform geometry -----------------------
// Units are millimeters. Z=0 is the motor shaft plane. Each crank rotates in
// its radial vertical plane; 180 deg points inward at neutral.
const float TABLE_ROD_DIAMETER_MM = 238.0;
const float TABLE_ROD_RADIUS_MM = 119.0;
const float CRANK_RADIUS_MM = 30.0;
const float ARM_LENGTH_MM = 110.0;
const float BASE_MOTOR_RADIUS_MM = 149.0;
const float BASE_MOTOR_DIAMETER_MM = 298.0;
const float NEUTRAL_TOP_Z_MM = 110.0;
const float LEG_AZIMUTH_DEG[AXES] = {0.0, 120.0, 240.0};
// If leg 1 is at top / +Y in CAD, use:
// const float LEG_AZIMUTH_DEG[AXES] = {90.0, 210.0, 330.0};
const float ROD_END_LIMIT_DEG = 14.0;
const float SUPPORT_STROKE_MM = 20.66;
const float NEUTRAL_CRANK_DEG = 180.0;
// The physical zero procedure starts with the platform at its highest
// symmetric position, not at neutral. For this geometry that top position is
// about 102.37 deg crank angle.
const float ZERO_CRANK_DEG = 102.37;
const float ZERO_CRANK_DELTA_DEG = ZERO_CRANK_DEG - NEUTRAL_CRANK_DEG;

struct Vec3 {
  float x;
  float y;
  float z;
};

struct AxisState {
  long currentSteps;
  long targetSteps;
};

Vec3 motorShaft[AXES];
Vec3 topRodNeutral[AXES];
AxisState axis[AXES];
bool axisEnabled[AXES] = {false, false, false};
unsigned long stepIntervalUs[AXES];
String line;
float currentRollDeg = 0.0;
float currentPitchDeg = 0.0;
float currentHeaveMm = 0.0;
float rollVelocityDegS = 0.0;
float pitchVelocityDegS = 0.0;
float heaveVelocityMmS = 0.0;
bool velocityMode = false;
unsigned long lastVelocityUpdateMs = 0;

const float MAX_ROLL_DEG = 10.0;
const float MAX_PITCH_DEG = 10.0;
const float MIN_HEAVE_MM = -8.0;
const float MAX_HEAVE_MM = 8.0;
const unsigned long VELOCITY_UPDATE_MS = 25;
const float VELOCITY_EPSILON = 0.001;
unsigned long velocityStepAccumulator[AXES] = {0, 0, 0};
unsigned long lastVelocityStepUs = 0;

float degToRad(float deg) {
  return deg * PI / 180.0;
}

float clampFloat(float value, float low, float high) {
  if (value < low) {
    return low;
  }
  if (value > high) {
    return high;
  }
  return value;
}

uint8_t logicLevel(bool active, bool activeLow) {
  return activeLow ? (active ? LOW : HIGH) : (active ? HIGH : LOW);
}

bool anyAxisEnabled() {
  for (uint8_t i = 0; i < AXES; i++) {
    if (axisEnabled[i]) {
      return true;
    }
  }
  return false;
}

void setAxisEnable(uint8_t i, bool on) {
  axisEnabled[i] = on;
  digitalWrite(ENA_PIN[i], logicLevel(on, ENA_ACTIVE_LOW));
}

void setEnable(bool on) {
  for (uint8_t i = 0; i < AXES; i++) {
    setAxisEnable(i, on);
  }
}

bool limitHit(uint8_t i) {
  if (!USE_LIMITS) {
    return false;
  }
  int state = digitalRead(LIMIT_PIN[i]);
  return LIMIT_ACTIVE_LOW ? (state == LOW) : (state == HIGH);
}

void buildGeometry() {
  for (uint8_t i = 0; i < AXES; i++) {
    float legAngle = degToRad(LEG_AZIMUTH_DEG[i]);

    motorShaft[i].x = BASE_MOTOR_RADIUS_MM * cos(legAngle);
    motorShaft[i].y = BASE_MOTOR_RADIUS_MM * sin(legAngle);
    motorShaft[i].z = 0.0;

    topRodNeutral[i].x = TABLE_ROD_RADIUS_MM * cos(legAngle);
    topRodNeutral[i].y = TABLE_ROD_RADIUS_MM * sin(legAngle);
    topRodNeutral[i].z = 0.0;
  }
}

Vec3 rotateRollPitch(Vec3 p, float rollRad, float pitchRad) {
  // Roll about X, then pitch about Y. Yaw and XY translation are fixed for a
  // 3-axis tilt/heave platform.
  float cr = cos(rollRad);
  float sr = sin(rollRad);
  float cp = cos(pitchRad);
  float sp = sin(pitchRad);

  float x1 = p.x;
  float y1 = p.y * cr - p.z * sr;
  float z1 = p.y * sr + p.z * cr;

  Vec3 out;
  out.x = x1 * cp + z1 * sp;
  out.y = y1;
  out.z = -x1 * sp + z1 * cp;
  return out;
}

Vec3 topRodPosition(uint8_t i, float rollDeg, float pitchDeg, float heaveMm) {
  Vec3 p = rotateRollPitch(topRodNeutral[i], degToRad(rollDeg), degToRad(pitchDeg));
  p.z += NEUTRAL_TOP_Z_MM + heaveMm;
  return p;
}

float normalizeDeg(float deg) {
  while (deg > 180.0) {
    deg -= 360.0;
  }
  while (deg < -180.0) {
    deg += 360.0;
  }
  return deg;
}

long crankAngleDeltaToSteps(uint8_t i, float crankDeltaDeg) {
  return lround(crankDeltaDeg * STEPS_PER_CRANK_REV[i] / 360.0);
}

float stepsToCrankAngleDelta(uint8_t i, long steps) {
  return steps * 360.0 / STEPS_PER_CRANK_REV[i];
}

Vec3 crankPinPosition(uint8_t i, float crankDeg) {
  float legAngle = degToRad(LEG_AZIMUTH_DEG[i]);
  float crankRad = degToRad(crankDeg);

  Vec3 p;
  p.x = motorShaft[i].x + CRANK_RADIUS_MM * cos(crankRad) * cos(legAngle);
  p.y = motorShaft[i].y + CRANK_RADIUS_MM * cos(crankRad) * sin(legAngle);
  p.z = CRANK_RADIUS_MM * sin(crankRad);
  return p;
}

float rodEndMisalignmentDeg(Vec3 top, Vec3 pin) {
  float dx = top.x - pin.x;
  float dy = top.y - pin.y;
  float dz = top.z - pin.z;
  float horizontal = sqrt(dx * dx + dy * dy);
  return atan2(horizontal, dz) * 180.0 / PI;
}

bool solveCrankAngle(uint8_t i, Vec3 top, float *crankDeg, float *misalignmentDeg) {
  float legAngle = degToRad(LEG_AZIMUTH_DEG[i]);
  float ux = cos(legAngle);
  float uy = sin(legAngle);
  float vx = -sin(legAngle);
  float vy = cos(legAngle);

  float topRadial = top.x * ux + top.y * uy;
  float topTangential = top.x * vx + top.y * vy;
  float tangentialSq = topTangential * topTangential;
  float armSq = ARM_LENGTH_MM * ARM_LENGTH_MM;
  if (tangentialSq > armSq) {
    return false;
  }

  float effectiveArm = sqrt(armSq - tangentialSq);
  float a = topRadial - BASE_MOTOR_RADIUS_MM;
  float b = top.z;
  float targetRadius = sqrt(a * a + b * b);
  if (targetRadius < 0.001) {
    return false;
  }

  float cosTerm = (targetRadius * targetRadius + CRANK_RADIUS_MM * CRANK_RADIUS_MM - effectiveArm * effectiveArm) /
                  (2.0 * CRANK_RADIUS_MM * targetRadius);
  if (cosTerm < -1.0 || cosTerm > 1.0) {
    return false;
  }

  cosTerm = clampFloat(cosTerm, -1.0, 1.0);
  float phi = atan2(b, a);
  float alpha = acos(cosTerm);
  float candidate0 = (phi + alpha) * 180.0 / PI;
  float candidate1 = (phi - alpha) * 180.0 / PI;
  float delta0 = normalizeDeg(candidate0 - NEUTRAL_CRANK_DEG);
  float delta1 = normalizeDeg(candidate1 - NEUTRAL_CRANK_DEG);

  *crankDeg = (fabs(delta0) <= fabs(delta1)) ? candidate0 : candidate1;
  Vec3 pin = crankPinPosition(i, *crankDeg);
  *misalignmentDeg = rodEndMisalignmentDeg(top, pin);
  return true;
}

long crankDeltaToSteps(uint8_t i, float crankDeltaDeg) {
  return crankAngleDeltaToSteps(i, crankDeltaDeg);
}

float stepsToCrankDelta(uint8_t i, long steps) {
  return stepsToCrankAngleDelta(i, steps);
}

void moveAxesToTargets(long target[AXES]);

void configureStepPulseTiming() {
  for (uint8_t i = 0; i < AXES; i++) {
    float stepsPerSecond = MAX_CRANK_SPEED_DEG_S * STEPS_PER_CRANK_REV[i] / 360.0;
    if (stepsPerSecond < 1.0) {
      stepsPerSecond = 1.0;
    }

    stepIntervalUs[i] = (unsigned long)lround(1000000.0 / stepsPerSecond);
    if (stepIntervalUs[i] <= STEP_PULSE_WIDTH_US) {
      stepIntervalUs[i] = STEP_PULSE_WIDTH_US + 1;
    }
  }
}

unsigned long absLong(long value) {
  return value < 0 ? (unsigned long)(-value) : (unsigned long)value;
}

void stepAxisBlocking(uint8_t i, long steps) {
  if (steps == 0) {
    return;
  }

  bool dirForward = steps > 0;
  if (DIR_INVERT[i]) {
    dirForward = !dirForward;
  }

  digitalWrite(DIR_PIN[i], dirForward ? HIGH : LOW);
  delayMicroseconds(5);

  unsigned long pulseCount = absLong(steps);
  unsigned long lowDelayUs = stepIntervalUs[i] - STEP_PULSE_WIDTH_US;
  while (pulseCount > 0) {
    digitalWrite(PLS_PIN[i], HIGH);
    delayMicroseconds(STEP_PULSE_WIDTH_US);
    digitalWrite(PLS_PIN[i], LOW);
    delayMicroseconds(lowDelayUs);
    pulseCount--;
  }
}

void moveAxisToTarget(uint8_t i) {
  long target[AXES] = {axis[0].currentSteps, axis[1].currentSteps, axis[2].currentSteps};
  target[i] = axis[i].targetSteps;
  moveAxesToTargets(target);
}

bool jogAxis(uint8_t i, long pulses) {
  if (!axisEnabled[i]) {
    Serial.println(F("ERR enable axis first"));
    return false;
  }

  if (pulses < 0 && limitHit(i)) {
    Serial.println(F("ERR limit hit"));
    return false;
  }

  stepAxisBlocking(i, pulses);
  axis[i].currentSteps += pulses;
  axis[i].targetSteps = axis[i].currentSteps;
  return true;
}

void moveAxesToTargets(long target[AXES]) {
  long delta[AXES];
  int direction[AXES];
  unsigned long remaining[AXES];
  unsigned long accumulator[AXES] = {0, 0, 0};
  unsigned long maxSteps = 0;
  unsigned long tickIntervalUs = STEP_PULSE_WIDTH_US + 1;
  bool active[AXES];

  for (uint8_t i = 0; i < AXES; i++) {
    active[i] = axisEnabled[i] && axis[i].currentSteps != target[i];
    if (!active[i]) {
      delta[i] = 0;
      direction[i] = 0;
      remaining[i] = 0;
      continue;
    }

    delta[i] = target[i] - axis[i].currentSteps;
    if (delta[i] < 0 && limitHit(i)) {
      axis[i].targetSteps = axis[i].currentSteps;
      active[i] = false;
      delta[i] = 0;
      direction[i] = 0;
      remaining[i] = 0;
      continue;
    }

    direction[i] = delta[i] > 0 ? 1 : -1;
    remaining[i] = absLong(delta[i]);
    if (remaining[i] > maxSteps) {
      maxSteps = remaining[i];
    }
  }

  if (maxSteps == 0) {
    return;
  }

  for (uint8_t i = 0; i < AXES; i++) {
    if (!active[i]) {
      continue;
    }

    bool dirForward = direction[i] > 0;
    if (DIR_INVERT[i]) {
      dirForward = !dirForward;
    }
    digitalWrite(DIR_PIN[i], dirForward ? HIGH : LOW);

    unsigned long requiredTickUs = (stepIntervalUs[i] * remaining[i] + maxSteps - 1) / maxSteps;
    if (requiredTickUs > tickIntervalUs) {
      tickIntervalUs = requiredTickUs;
    }
  }

  if (tickIntervalUs <= STEP_PULSE_WIDTH_US) {
    tickIntervalUs = STEP_PULSE_WIDTH_US + 1;
  }
  delayMicroseconds(5);

  unsigned long lowDelayUs = tickIntervalUs - STEP_PULSE_WIDTH_US;
  for (unsigned long tick = 0; tick < maxSteps; tick++) {
    bool pulse[AXES] = {false, false, false};

    for (uint8_t i = 0; i < AXES; i++) {
      if (!active[i] || remaining[i] == 0) {
        continue;
      }

      accumulator[i] += remaining[i];
      if (accumulator[i] >= maxSteps) {
        accumulator[i] -= maxSteps;
        pulse[i] = true;
        digitalWrite(PLS_PIN[i], HIGH);
      }
    }

    delayMicroseconds(STEP_PULSE_WIDTH_US);

    for (uint8_t i = 0; i < AXES; i++) {
      if (pulse[i]) {
        digitalWrite(PLS_PIN[i], LOW);
        axis[i].currentSteps += direction[i];
      }
    }

    delayMicroseconds(lowDelayUs);
  }
}

void moveToTargets() {
  long target[AXES] = {axis[0].targetSteps, axis[1].targetSteps, axis[2].targetSteps};
  moveAxesToTargets(target);
}

void setTargets(long t0, long t1, long t2) {
  axis[0].targetSteps = t0;
  axis[1].targetSteps = t1;
  axis[2].targetSteps = t2;
  moveToTargets();
}

bool moving() {
  for (uint8_t i = 0; i < AXES; i++) {
    if (axisEnabled[i] && axis[i].currentSteps != axis[i].targetSteps) {
      return true;
    }
  }
  return false;
}

void printStatus() {
  Serial.print(F("OK enabled "));
  Serial.print(anyAxisEnabled() ? F("1") : F("0"));
  Serial.print(F(" moving "));
  Serial.print(moving() ? F("1") : F("0"));
  for (uint8_t i = 0; i < AXES; i++) {
    Serial.print(F(" axis"));
    Serial.print(i);
    Serial.print(F("_enabled "));
    Serial.print(axisEnabled[i] ? F("1") : F("0"));
    Serial.print(F(" axis"));
    Serial.print(i);
    Serial.print(F("_steps "));
    Serial.print(axis[i].currentSteps);
    Serial.print(F(" axis"));
    Serial.print(i);
    Serial.print(F("_target "));
    Serial.print(axis[i].targetSteps);
    Serial.print(F(" axis"));
    Serial.print(i);
    Serial.print(F("_deg "));
    Serial.print(stepsToCrankDelta(i, axis[i].currentSteps), 3);
    Serial.print(F(" axis"));
    Serial.print(i);
    Serial.print(F("_target_deg "));
    Serial.print(stepsToCrankDelta(i, axis[i].targetSteps), 3);
  }
  Serial.print(F(" roll "));
  Serial.print(currentRollDeg, 3);
  Serial.print(F(" pitch "));
  Serial.print(currentPitchDeg, 3);
  Serial.print(F(" heave "));
  Serial.print(currentHeaveMm, 3);
  Serial.print(F(" vel_roll "));
  Serial.print(rollVelocityDegS, 3);
  Serial.print(F(" vel_pitch "));
  Serial.print(pitchVelocityDegS, 3);
  Serial.print(F(" vel_heave "));
  Serial.print(heaveVelocityMmS, 3);
  Serial.println();
}

void printHelp() {
  Serial.println(F("UIM5756PM 3-axis Stewart controller"));
  Serial.println(F("Commands:"));
  Serial.println(F("  pose <roll_deg> <pitch_deg> <heave_mm>"));
  Serial.println(F("  vel <roll_deg_s> <pitch_deg_s> <heave_mm_s>"));
  Serial.println(F("  angle <a0_deg> <a1_deg> <a2_deg> target crank deltas from neutral"));
  Serial.println(F("  steps <s0> <s1> <s2>          target crank deltas in steps"));
  Serial.println(F("  jog <axis> <pulses>           raw open-loop motor jog"));
  Serial.println(F("  zero                          set current position as top calibration"));
  Serial.println(F("  enable [axis] | disable [axis]"));
  Serial.println(F("  status"));
  Serial.println(F("  help"));
}

int splitTokens(char *input, char *tokens[], int maxTokens) {
  int count = 0;
  char *token = strtok(input, " \t\r\n,");
  while (token != NULL && count < maxTokens) {
    tokens[count++] = token;
    token = strtok(NULL, " \t\r\n,");
  }
  return count;
}

bool solvePoseTargets(float roll, float pitch, float heave, long target[AXES]) {
  for (uint8_t i = 0; i < AXES; i++) {
    Vec3 top = topRodPosition(i, roll, pitch, heave);
    float crankDeg;
    float misalignmentDeg;
    if (!solveCrankAngle(i, top, &crankDeg, &misalignmentDeg)) {
      Serial.println(F("ERR pose unreachable"));
      return false;
    }

    if (misalignmentDeg > ROD_END_LIMIT_DEG) {
      Serial.println(F("ERR pose exceeds rod-end angle"));
      return false;
    }

    float crankDeltaDeg = normalizeDeg(crankDeg - NEUTRAL_CRANK_DEG);
    target[i] = crankDeltaToSteps(i, crankDeltaDeg);
  }
  return true;
}

bool moveToPose(float roll, float pitch, float heave) {
  long target[AXES];
  if (!solvePoseTargets(roll, pitch, heave, target)) {
    return false;
  }
  setTargets(target[0], target[1], target[2]);
  currentRollDeg = roll;
  currentPitchDeg = pitch;
  currentHeaveMm = heave;
  return true;
}

bool setPoseTarget(float roll, float pitch, float heave) {
  long target[AXES];
  if (!solvePoseTargets(roll, pitch, heave, target)) {
    return false;
  }
  axis[0].targetSteps = target[0];
  axis[1].targetSteps = target[1];
  axis[2].targetSteps = target[2];
  currentRollDeg = roll;
  currentPitchDeg = pitch;
  currentHeaveMm = heave;
  return true;
}

void stopVelocityMode() {
  rollVelocityDegS = 0.0;
  pitchVelocityDegS = 0.0;
  heaveVelocityMmS = 0.0;
  velocityMode = false;
  for (uint8_t i = 0; i < AXES; i++) {
    axis[i].targetSteps = axis[i].currentSteps;
    velocityStepAccumulator[i] = 0;
  }
}

bool parsePose(char *tokens[], int count) {
  if (count != 4) {
    Serial.println(F("ERR pose needs roll pitch heave"));
    return false;
  }

  stopVelocityMode();
  float roll = atof(tokens[1]);
  float pitch = atof(tokens[2]);
  float heave = atof(tokens[3]);

  if (!moveToPose(roll, pitch, heave)) {
    return false;
  }

  Serial.print(F("OK pose targets"));
  for (uint8_t i = 0; i < AXES; i++) {
    Serial.print(F(" axis"));
    Serial.print(i);
    Serial.print(F("_steps "));
    Serial.print(axis[i].targetSteps);
    Serial.print(F(" axis"));
    Serial.print(i);
    Serial.print(F("_deg "));
    Serial.print(stepsToCrankDelta(i, axis[i].targetSteps), 3);
  }
  Serial.println();
  return true;
}

bool parseVelocity(char *tokens[], int count) {
  if (count != 4) {
    Serial.println(F("ERR vel needs roll_deg_s pitch_deg_s heave_mm_s"));
    return false;
  }

  rollVelocityDegS = atof(tokens[1]);
  pitchVelocityDegS = atof(tokens[2]);
  heaveVelocityMmS = atof(tokens[3]);
  velocityMode = fabs(rollVelocityDegS) > VELOCITY_EPSILON ||
                 fabs(pitchVelocityDegS) > VELOCITY_EPSILON ||
                 fabs(heaveVelocityMmS) > VELOCITY_EPSILON;
  lastVelocityUpdateMs = millis();

  Serial.print(F("OK vel roll "));
  Serial.print(rollVelocityDegS, 3);
  Serial.print(F(" pitch "));
  Serial.print(pitchVelocityDegS, 3);
  Serial.print(F(" heave "));
  Serial.println(heaveVelocityMmS, 3);
  return true;
}

bool parseAngles(char *tokens[], int count) {
  if (count != 4) {
    Serial.println(F("ERR angle needs three crank deltas in deg"));
    return false;
  }

  stopVelocityMode();
  long target[AXES];
  for (uint8_t i = 0; i < AXES; i++) {
    float crankDeltaDeg = atof(tokens[i + 1]);
    target[i] = crankDeltaToSteps(i, crankDeltaDeg);
  }

  setTargets(target[0], target[1], target[2]);
  Serial.print(F("OK angle targets"));
  for (uint8_t i = 0; i < AXES; i++) {
    Serial.print(F(" axis"));
    Serial.print(i);
    Serial.print(F("_steps "));
    Serial.print(axis[i].targetSteps);
    Serial.print(F(" axis"));
    Serial.print(i);
    Serial.print(F("_deg "));
    Serial.print(stepsToCrankDelta(i, axis[i].targetSteps), 3);
  }
  Serial.println();
  return true;
}

bool parseSteps(char *tokens[], int count) {
  if (count != 4) {
    Serial.println(F("ERR steps needs three step targets"));
    return false;
  }

  stopVelocityMode();
  setTargets(atol(tokens[1]), atol(tokens[2]), atol(tokens[3]));
  Serial.print(F("OK steps targets"));
  for (uint8_t i = 0; i < AXES; i++) {
    Serial.print(F(" axis"));
    Serial.print(i);
    Serial.print(F("_steps "));
    Serial.print(axis[i].targetSteps);
  }
  Serial.println();
  return true;
}

bool parseJog(char *tokens[], int count) {
  if (count != 3) {
    Serial.println(F("ERR jog needs axis pulses"));
    return false;
  }

  long axisIndex = atol(tokens[1]);
  if (axisIndex < 0 || axisIndex >= AXES) {
    Serial.println(F("ERR jog axis must be 0, 1, or 2"));
    return false;
  }

  uint8_t i = (uint8_t)axisIndex;
  long pulses = atol(tokens[2]);
  stopVelocityMode();
  if (!jogAxis(i, pulses)) {
    return false;
  }

  Serial.print(F("OK jog axis"));
  Serial.print(i);
  Serial.print(F("_steps "));
  Serial.print(axis[i].currentSteps);
  Serial.print(F(" axis"));
  Serial.print(i);
  Serial.print(F("_deg "));
  Serial.print(stepsToCrankDelta(i, axis[i].currentSteps), 3);
  Serial.println();
  return true;
}

void zeroPosition() {
  stopVelocityMode();
  for (uint8_t i = 0; i < AXES; i++) {
    long topSteps = crankDeltaToSteps(i, ZERO_CRANK_DELTA_DEG);
    axis[i].currentSteps = topSteps;
    axis[i].targetSteps = topSteps;
  }
  currentRollDeg = 0.0;
  currentPitchDeg = 0.0;
  currentHeaveMm = 0.0;
  Serial.println(F("OK zero"));
}

bool parseAxisIndex(char *token, uint8_t *axisIndex) {
  long value = atol(token);
  if (value < 0 || value >= AXES) {
    Serial.println(F("ERR axis must be 0, 1, or 2"));
    return false;
  }

  *axisIndex = (uint8_t)value;
  return true;
}

bool parseEnableCommand(char *tokens[], int count, bool on) {
  if (count == 1) {
    setEnable(on);
    if (!on) {
      stopVelocityMode();
    }
    if (on) {
      moveToTargets();
    }
    Serial.println(on ? F("OK enable") : F("OK disable"));
    return true;
  }

  if (count != 2) {
    Serial.println(on ? F("ERR enable needs optional axis") : F("ERR disable needs optional axis"));
    return false;
  }

  uint8_t i;
  if (!parseAxisIndex(tokens[1], &i)) {
    return false;
  }

  setAxisEnable(i, on);
  if (!on) {
    stopVelocityMode();
  }
  if (on) {
    moveAxisToTarget(i);
  }

  Serial.print(on ? F("OK enable axis") : F("OK disable axis"));
  Serial.println(i);
  return true;
}

void handleCommand(String command) {
  command.trim();
  if (command.length() == 0) {
    return;
  }

  char buffer[96];
  command.toCharArray(buffer, sizeof(buffer));

  char *tokens[5];
  int count = splitTokens(buffer, tokens, 5);
  if (count == 0) {
    return;
  }

  String cmd = tokens[0];
  cmd.toLowerCase();

  if (cmd == "pose" || cmd == "p") {
    parsePose(tokens, count);
  } else if (cmd == "vel" || cmd == "v") {
    parseVelocity(tokens, count);
  } else if (cmd == "angle" || cmd == "a") {
    parseAngles(tokens, count);
  } else if (cmd == "steps" || cmd == "s") {
    parseSteps(tokens, count);
  } else if (cmd == "jog" || cmd == "j") {
    parseJog(tokens, count);
  } else if (cmd == "zero") {
    zeroPosition();
  } else if (cmd == "enable" || cmd == "on") {
    parseEnableCommand(tokens, count, true);
  } else if (cmd == "disable" || cmd == "off") {
    parseEnableCommand(tokens, count, false);
  } else if (cmd == "status") {
    printStatus();
  } else if (cmd == "help" || cmd == "?") {
    printHelp();
  } else {
    Serial.println(F("ERR unknown command"));
  }
}

void updateVelocityMotion() {
  if (!velocityMode || !anyAxisEnabled()) {
    lastVelocityUpdateMs = millis();
    return;
  }

  unsigned long nowMs = millis();
  unsigned long elapsedMs = nowMs - lastVelocityUpdateMs;
  if (elapsedMs < VELOCITY_UPDATE_MS) {
    return;
  }
  lastVelocityUpdateMs = nowMs;

  float dt = elapsedMs / 1000.0;
  float nextRoll = clampFloat(currentRollDeg + rollVelocityDegS * dt, -MAX_ROLL_DEG, MAX_ROLL_DEG);
  float nextPitch = clampFloat(currentPitchDeg + pitchVelocityDegS * dt, -MAX_PITCH_DEG, MAX_PITCH_DEG);
  float nextHeave = clampFloat(currentHeaveMm + heaveVelocityMmS * dt, MIN_HEAVE_MM, MAX_HEAVE_MM);

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

void serviceVelocitySteppers() {
  if (!velocityMode || !anyAxisEnabled()) {
    lastVelocityStepUs = micros();
    for (uint8_t i = 0; i < AXES; i++) {
      velocityStepAccumulator[i] = 0;
    }
    return;
  }

  long delta[AXES];
  int direction[AXES];
  unsigned long remaining[AXES];
  unsigned long maxSteps = 0;
  bool active[AXES];

  for (uint8_t i = 0; i < AXES; i++) {
    active[i] = axisEnabled[i] && axis[i].currentSteps != axis[i].targetSteps;
    if (!active[i]) {
      delta[i] = 0;
      direction[i] = 0;
      remaining[i] = 0;
      velocityStepAccumulator[i] = 0;
      continue;
    }

    delta[i] = axis[i].targetSteps - axis[i].currentSteps;
    if (delta[i] < 0 && limitHit(i)) {
      axis[i].targetSteps = axis[i].currentSteps;
      active[i] = false;
      delta[i] = 0;
      direction[i] = 0;
      remaining[i] = 0;
      velocityStepAccumulator[i] = 0;
      continue;
    }

    direction[i] = delta[i] > 0 ? 1 : -1;
    remaining[i] = absLong(delta[i]);
    if (remaining[i] > maxSteps) {
      maxSteps = remaining[i];
    }
  }

  if (maxSteps == 0) {
    return;
  }

  unsigned long tickIntervalUs = STEP_PULSE_WIDTH_US + 1;
  for (uint8_t i = 0; i < AXES; i++) {
    if (!active[i]) {
      continue;
    }

    bool dirForward = direction[i] > 0;
    if (DIR_INVERT[i]) {
      dirForward = !dirForward;
    }
    digitalWrite(DIR_PIN[i], dirForward ? HIGH : LOW);

    unsigned long requiredTickUs = (stepIntervalUs[i] * remaining[i] + maxSteps - 1) / maxSteps;
    if (requiredTickUs > tickIntervalUs) {
      tickIntervalUs = requiredTickUs;
    }
  }

  if (tickIntervalUs <= STEP_PULSE_WIDTH_US) {
    tickIntervalUs = STEP_PULSE_WIDTH_US + 1;
  }

  unsigned long nowUs = micros();
  if ((unsigned long)(nowUs - lastVelocityStepUs) < tickIntervalUs) {
    return;
  }
  lastVelocityStepUs = nowUs;

  bool pulse[AXES] = {false, false, false};
  for (uint8_t i = 0; i < AXES; i++) {
    if (!active[i] || remaining[i] == 0) {
      continue;
    }

    velocityStepAccumulator[i] += remaining[i];
    if (velocityStepAccumulator[i] >= maxSteps) {
      velocityStepAccumulator[i] -= maxSteps;
      pulse[i] = true;
      digitalWrite(PLS_PIN[i], HIGH);
    }
  }

  delayMicroseconds(STEP_PULSE_WIDTH_US);

  for (uint8_t i = 0; i < AXES; i++) {
    if (pulse[i]) {
      digitalWrite(PLS_PIN[i], LOW);
      axis[i].currentSteps += direction[i];
    }
  }
}

void readSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (line.length() > 0) {
        handleCommand(line);
        line = "";
      }
    } else if (line.length() < 95) {
      line += c;
    }
  }
}

void setup() {
  Serial.begin(115200);

  for (uint8_t i = 0; i < AXES; i++) {
    pinMode(PLS_PIN[i], OUTPUT);
    pinMode(DIR_PIN[i], OUTPUT);
    if (USE_LIMITS) {
      pinMode(LIMIT_PIN[i], LIMIT_ACTIVE_LOW ? INPUT_PULLUP : INPUT);
    }
    long topSteps = crankDeltaToSteps(i, ZERO_CRANK_DELTA_DEG);
    axis[i].currentSteps = topSteps;
    axis[i].targetSteps = topSteps;
    pinMode(ENA_PIN[i], OUTPUT);
  }

  configureStepPulseTiming();
  setEnable(false);

  buildGeometry();
  Serial.println(F("UIM5756PM Stewart controller ready. Send 'help'."));
}

void loop() {
  readSerial();
  updateVelocityMotion();
  serviceVelocitySteppers();
}
