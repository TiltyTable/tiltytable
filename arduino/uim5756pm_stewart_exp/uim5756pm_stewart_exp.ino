/*
  Experimental full-rotation Stewart executor (Uno R3)

  Dedicated protocol; NOT wire-compatible with production firmware.
  Host software owns all IK, heave optimization, and branch continuity.
  This sketch only calibrates, gates, validates, and executes absolute steps.

  115200 baud, newline terminated:
    EXP?
    STATUS
    CAL BEGIN
    CAL JOG <axis> <pulses>
    CAL MARK <axis>
    CAL FINISH
    ARM CONFIRM
    TARGET <s0> <s1> <s2> <roll> <pitch> <heave>
    HOLD
    ABORT
    DISABLE
*/

#include <AccelStepper.h>
#include <Arduino.h>
#include <EEPROM.h>
#include <avr/wdt.h>
#include <math.h>
#include <stdlib.h>

const uint8_t AXES = 3;
const uint8_t PLS_PIN[AXES] = {2, 7, 11};
const uint8_t DIR_PIN[AXES] = {3, 8, 12};
const uint8_t ENA_PIN[AXES] = {4, 9, 13};
const bool DIR_INVERT[AXES] = {false, false, false};
const bool ENA_ACTIVE_LOW = true;

const float STEPS_PER_CRANK_REV = 16000.0;  // MCS=4, 20:1 gearbox
// Runtime-configurable profile. 90 deg/s at MCS=4 is 4000 pulses/s, the
// documented practical ceiling for AccelStepper on a 16 MHz Uno.
const float DEFAULT_CRANK_SPEED_DEG_S = 40.0;
const float DEFAULT_CRANK_ACCEL_DEG_S2 = 120.0;
const float MIN_CRANK_SPEED_DEG_S = 1.0;
const float MAX_CRANK_SPEED_DEG_S = 90.0;
const float MIN_CRANK_ACCEL_DEG_S2 = 1.0;
const float MAX_CRANK_ACCEL_DEG_S2 = 500.0;
const float MAX_TARGET_DELTA_DEG = 12.0;
const long MAX_TARGET_DELTA_STEPS =
  lround(MAX_TARGET_DELTA_DEG * STEPS_PER_CRANK_REV / 360.0);
const long MAX_CAL_JOG_STEPS = 3200;

const uint32_t EXP_MAGIC = 0x54545845UL;  // "TTXE"
const uint16_t EXP_VERSION = 1;
const int EXP_EEPROM_OFFSET = 128;

uint8_t resetCause __attribute__((section(".noinit")));
void captureResetCause(void) __attribute__((naked, section(".init3")));
void captureResetCause(void) {
  resetCause = MCUSR;
  MCUSR = 0;
  wdt_disable();
}

struct PersistedExperiment {
  uint32_t magic;
  uint16_t version;
  long steps[AXES];
  float rollDeg;
  float pitchDeg;
  float heaveMm;
  uint16_t checksum;
};

AccelStepper stepper[AXES] = {
  AccelStepper(AccelStepper::DRIVER, PLS_PIN[0], DIR_PIN[0]),
  AccelStepper(AccelStepper::DRIVER, PLS_PIN[1], DIR_PIN[1]),
  AccelStepper(AccelStepper::DRIVER, PLS_PIN[2], DIR_PIN[2]),
};

long desiredTarget[AXES] = {0, 0, 0};
bool axisEnabled[AXES] = {false, false, false};
bool axisMarked[AXES] = {false, false, false};
bool calibrating = false;
bool calibrated = false;
bool armed = false;
bool restored = false;
float currentRollDeg = 0.0;
float currentPitchDeg = 0.0;
float currentHeaveMm = 30.0;
float profileSpeedDegS = DEFAULT_CRANK_SPEED_DEG_S;
float profileAccelDegS2 = DEFAULT_CRANK_ACCEL_DEG_S2;
String line;

uint8_t logicLevel(bool active, bool activeLow) {
  return activeLow ? (active ? LOW : HIGH) : (active ? HIGH : LOW);
}

void setAxisEnable(uint8_t axis, bool enabled) {
  axisEnabled[axis] = enabled;
  digitalWrite(ENA_PIN[axis], logicLevel(enabled, ENA_ACTIVE_LOW));
}

void setEnable(bool enabled) {
  for (uint8_t axis = 0; axis < AXES; axis++) setAxisEnable(axis, enabled);
}

bool moving() {
  for (uint8_t axis = 0; axis < AXES; axis++) {
    if (axisEnabled[axis] && stepper[axis].distanceToGo() != 0) return true;
  }
  return false;
}

bool anyAxisEnabled() {
  for (uint8_t axis = 0; axis < AXES; axis++) {
    if (axisEnabled[axis]) return true;
  }
  return false;
}

void holdCurrent() {
  for (uint8_t axis = 0; axis < AXES; axis++) {
    desiredTarget[axis] = stepper[axis].currentPosition();
    stepper[axis].moveTo(desiredTarget[axis]);
  }
  setEnable(true);
}

uint16_t persistedChecksum(const uint8_t *bytes, size_t length) {
  uint16_t value = 0x7E57;
  for (size_t index = 0; index < length; index++) {
    value = (uint16_t)((value << 5) | (value >> 11));
    value ^= bytes[index];
  }
  return value;
}

void clearPersisted() {
  PersistedExperiment empty = {};
  EEPROM.put(EXP_EEPROM_OFFSET, empty);
  restored = false;
}

bool savePersisted() {
  if (!calibrated || moving()) return false;
  PersistedExperiment state = {};
  state.magic = EXP_MAGIC;
  state.version = EXP_VERSION;
  for (uint8_t axis = 0; axis < AXES; axis++) {
    state.steps[axis] = stepper[axis].currentPosition();
  }
  state.rollDeg = currentRollDeg;
  state.pitchDeg = currentPitchDeg;
  state.heaveMm = currentHeaveMm;
  state.checksum = persistedChecksum(
    reinterpret_cast<const uint8_t *>(&state),
    sizeof(PersistedExperiment) - sizeof(state.checksum)
  );
  EEPROM.put(EXP_EEPROM_OFFSET, state);
  return true;
}

bool loadPersisted() {
  PersistedExperiment state;
  EEPROM.get(EXP_EEPROM_OFFSET, state);
  if (state.magic != EXP_MAGIC || state.version != EXP_VERSION) return false;
  if (state.checksum != persistedChecksum(
        reinterpret_cast<const uint8_t *>(&state),
        sizeof(PersistedExperiment) - sizeof(state.checksum)
      )) return false;
  if (!isfinite(state.rollDeg) || !isfinite(state.pitchDeg) ||
      !isfinite(state.heaveMm)) return false;

  bool externalReset = (resetCause & _BV(EXTRF)) != 0;
  bool unsafeReset = (resetCause & (_BV(PORF) | _BV(BORF) | _BV(WDRF))) != 0;
  if (!externalReset || unsafeReset) return false;

  for (uint8_t axis = 0; axis < AXES; axis++) {
    stepper[axis].setCurrentPosition(state.steps[axis]);
    desiredTarget[axis] = state.steps[axis];
    axisMarked[axis] = true;
  }
  currentRollDeg = state.rollDeg;
  currentPitchDeg = state.pitchDeg;
  currentHeaveMm = state.heaveMm;
  calibrated = true;
  restored = true;
  return true;
}

void applyCoordinatedTargets(const long target[AXES]) {
  long maxDelta = 0;
  for (uint8_t axis = 0; axis < AXES; axis++) {
    long delta = labs(target[axis] - stepper[axis].currentPosition());
    if (delta > maxDelta) maxDelta = delta;
  }
  for (uint8_t axis = 0; axis < AXES; axis++) {
    desiredTarget[axis] = target[axis];
    long delta = labs(target[axis] - stepper[axis].currentPosition());
    float scale = maxDelta > 0 ? (float)delta / (float)maxDelta : 1.0;
    if (scale < 0.001) scale = 0.001;
    stepper[axis].setMaxSpeed(
      profileSpeedDegS * STEPS_PER_CRANK_REV / 360.0 * scale
    );
    stepper[axis].setAcceleration(
      profileAccelDegS2 * STEPS_PER_CRANK_REV / 360.0 * scale
    );
    stepper[axis].moveTo(target[axis]);
  }
}

int splitTokens(char *input, char *tokens[], int maxTokens) {
  int count = 0;
  char *token = strtok(input, " \t\r\n,");
  while (token && count < maxTokens) {
    tokens[count++] = token;
    token = strtok(NULL, " \t\r\n,");
  }
  return count;
}

bool parseAxis(const char *text, uint8_t *axis) {
  long value = atol(text);
  if (value < 0 || value >= AXES) {
    Serial.println(F("ERR AXIS"));
    return false;
  }
  *axis = (uint8_t)value;
  return true;
}

void printStatus() {
  Serial.print(F("OK STATUS exp=1 calibrated="));
  Serial.print(calibrated ? 1 : 0);
  Serial.print(F(" restored=")); Serial.print(restored ? 1 : 0);
  Serial.print(F(" calibrating=")); Serial.print(calibrating ? 1 : 0);
  Serial.print(F(" armed=")); Serial.print(armed ? 1 : 0);
  Serial.print(F(" enabled=")); Serial.print(
    axisEnabled[0] || axisEnabled[1] || axisEnabled[2] ? 1 : 0
  );
  Serial.print(F(" moving=")); Serial.print(moving() ? 1 : 0);
  for (uint8_t axis = 0; axis < AXES; axis++) {
    Serial.print(F(" s")); Serial.print(axis); Serial.print('=');
    Serial.print(stepper[axis].currentPosition());
    Serial.print(F(" t")); Serial.print(axis); Serial.print('=');
    Serial.print(desiredTarget[axis]);
    Serial.print(F(" m")); Serial.print(axis); Serial.print('=');
    Serial.print(axisMarked[axis] ? 1 : 0);
  }
  Serial.print(F(" roll=")); Serial.print(currentRollDeg, 4);
  Serial.print(F(" pitch=")); Serial.print(currentPitchDeg, 4);
  Serial.print(F(" heave=")); Serial.print(currentHeaveMm, 4);
  Serial.print(F(" vmax=")); Serial.print(profileSpeedDegS, 3);
  Serial.print(F(" amax=")); Serial.println(profileAccelDegS2, 3);
}

void printHelp() {
  Serial.println(F("UIM5756PM STEWART EXP v1"));
  Serial.println(F("EXP? | STATUS | HELP"));
  Serial.println(F("CAL BEGIN | CAL JOG axis pulses | CAL MARK axis | CAL FINISH"));
  Serial.println(F("ARM CONFIRM | TARGET s0 s1 s2 roll pitch heave"));
  Serial.println(F("PROFILE speed_deg_s accel_deg_s2 | PROFILE?"));
  Serial.println(F("HOLD | ABORT | DISABLE"));
}

void handleCommand(String command) {
  command.trim();
  if (!command.length()) return;
  char buffer[160];
  command.toCharArray(buffer, sizeof(buffer));
  char *tokens[10];
  int count = splitTokens(buffer, tokens, 10);
  if (!count) return;

  String first(tokens[0]);
  first.toUpperCase();

  if (first == "EXP?") {
    Serial.println(F("OK EXP UIM5756PM_STEWART_EXP 1"));
  } else if (first == "HELP" || first == "?") {
    printHelp();
  } else if (first == "STATUS") {
    printStatus();
  } else if (first == "PROFILE?") {
    Serial.print(F("OK PROFILE speed=")); Serial.print(profileSpeedDegS, 3);
    Serial.print(F(" accel=")); Serial.println(profileAccelDegS2, 3);
  } else if (first == "PROFILE") {
    if (count != 3 || moving()) {
      Serial.println(F("ERR PROFILE SYNTAX_OR_MOVING"));
      return;
    }
    float requestedSpeed = atof(tokens[1]);
    float requestedAccel = atof(tokens[2]);
    if (requestedSpeed < MIN_CRANK_SPEED_DEG_S ||
        requestedSpeed > MAX_CRANK_SPEED_DEG_S ||
        requestedAccel < MIN_CRANK_ACCEL_DEG_S2 ||
        requestedAccel > MAX_CRANK_ACCEL_DEG_S2) {
      Serial.println(F("ERR PROFILE RANGE speed=1..90 accel=1..500"));
      return;
    }
    profileSpeedDegS = requestedSpeed;
    profileAccelDegS2 = requestedAccel;
    for (uint8_t axis = 0; axis < AXES; axis++) {
      stepper[axis].setMaxSpeed(
        profileSpeedDegS * STEPS_PER_CRANK_REV / 360.0
      );
      stepper[axis].setAcceleration(
        profileAccelDegS2 * STEPS_PER_CRANK_REV / 360.0
      );
    }
    Serial.print(F("OK PROFILE speed=")); Serial.print(profileSpeedDegS, 3);
    Serial.print(F(" accel=")); Serial.println(profileAccelDegS2, 3);
  } else if (first == "CAL") {
    if (count < 2) {
      Serial.println(F("ERR CAL SYNTAX"));
      return;
    }
    String action(tokens[1]);
    action.toUpperCase();
    if (action == "BEGIN") {
      setEnable(false);
      armed = false;
      calibrating = true;
      calibrated = false;
      restored = false;
      clearPersisted();
      for (uint8_t axis = 0; axis < AXES; axis++) axisMarked[axis] = false;
      Serial.println(F("OK CAL BEGIN"));
    } else if (action == "JOG" && count == 4) {
      uint8_t axis;
      long pulses = atol(tokens[3]);
      if (!calibrating || !parseAxis(tokens[2], &axis)) {
        if (!calibrating) Serial.println(F("ERR CAL NOT_ACTIVE"));
        return;
      }
      if (labs(pulses) > MAX_CAL_JOG_STEPS || moving()) {
        Serial.println(F("ERR CAL JOG_LIMIT"));
        return;
      }
      setEnable(false);
      setAxisEnable(axis, true);
      desiredTarget[axis] = stepper[axis].currentPosition() + pulses;
      stepper[axis].moveTo(desiredTarget[axis]);
      Serial.print(F("OK CAL JOG axis=")); Serial.print(axis);
      Serial.print(F(" target=")); Serial.println(desiredTarget[axis]);
    } else if (action == "MARK" && count == 3) {
      uint8_t axis;
      if (!calibrating || !parseAxis(tokens[2], &axis)) {
        if (!calibrating) Serial.println(F("ERR CAL NOT_ACTIVE"));
        return;
      }
      if (moving()) {
        Serial.println(F("ERR MOVING"));
        return;
      }
      setAxisEnable(axis, false);
      stepper[axis].setCurrentPosition(0);
      desiredTarget[axis] = 0;
      axisMarked[axis] = true;
      Serial.print(F("OK CAL MARK axis=")); Serial.println(axis);
    } else if (action == "FINISH") {
      for (uint8_t axis = 0; axis < AXES; axis++) {
        if (!axisMarked[axis]) {
          Serial.print(F("ERR CAL UNMARKED axis=")); Serial.println(axis);
          return;
        }
      }
      setEnable(false);
      calibrating = false;
      calibrated = true;
      currentRollDeg = 0.0;
      currentPitchDeg = 0.0;
      currentHeaveMm = 30.0;
      savePersisted();
      Serial.println(F("OK CAL FINISH"));
    } else {
      Serial.println(F("ERR CAL SYNTAX"));
    }
  } else if (first == "ARM") {
    if (count != 2 || String(tokens[1]) != "CONFIRM") {
      Serial.println(F("ERR ARM CONFIRM_REQUIRED"));
    } else if (!calibrated || calibrating || moving()) {
      Serial.println(F("ERR ARM STATE"));
    } else {
      armed = true;
      setEnable(true);
      Serial.println(F("OK ARM"));
    }
  } else if (first == "TARGET") {
    if (count != 7) {
      Serial.println(F("ERR TARGET SYNTAX"));
      return;
    }
    if (!armed || !calibrated) {
      Serial.println(F("ERR TARGET DISARMED"));
      return;
    }
    long target[AXES] = {atol(tokens[1]), atol(tokens[2]), atol(tokens[3])};
    for (uint8_t axis = 0; axis < AXES; axis++) {
      if (labs(target[axis] - desiredTarget[axis]) > MAX_TARGET_DELTA_STEPS) {
        Serial.print(F("ERR TARGET JUMP axis=")); Serial.println(axis);
        return;
      }
    }
    currentRollDeg = atof(tokens[4]);
    currentPitchDeg = atof(tokens[5]);
    currentHeaveMm = atof(tokens[6]);
    applyCoordinatedTargets(target);
    Serial.print(F("OK TARGET"));
    for (uint8_t axis = 0; axis < AXES; axis++) {
      Serial.print(' '); Serial.print(target[axis]);
    }
    Serial.println();
  } else if (first == "HOLD" || first == "ABORT") {
    if (!calibrated || !anyAxisEnabled()) {
      armed = false;
      Serial.println(
        first == "ABORT" ? F("OK ABORT DISABLED") : F("OK HOLD DISABLED")
      );
      return;
    }
    bool wasMoving = moving();
    holdCurrent();
    armed = false;
    if (wasMoving) clearPersisted();
    else savePersisted();
    Serial.println(first == "ABORT" ? F("OK ABORT HOLDING") : F("OK HOLD"));
  } else if (first == "DISABLE") {
    setEnable(false);
    armed = false;
    Serial.println(F("OK DISABLE"));
  } else {
    Serial.println(F("ERR UNKNOWN"));
  }
}

void setup() {
  Serial.begin(115200);
  for (uint8_t axis = 0; axis < AXES; axis++) {
    stepper[axis].setPinsInverted(DIR_INVERT[axis], true, false);
    stepper[axis].setMinPulseWidth(5);
    stepper[axis].setMaxSpeed(
      profileSpeedDegS * STEPS_PER_CRANK_REV / 360.0
    );
    stepper[axis].setAcceleration(
      profileAccelDegS2 * STEPS_PER_CRANK_REV / 360.0
    );
    stepper[axis].setCurrentPosition(0);
    desiredTarget[axis] = 0;
    pinMode(ENA_PIN[axis], OUTPUT);
  }
  setEnable(false);
  bool didRestore = loadPersisted();
  Serial.println(F("UIM5756PM STEWART EXP ready; DISABLED."));
  Serial.println(
    didRestore
      ? F("Experimental state restored after external reset.")
      : F("Experimental calibration required.")
  );
  Serial.println(F("Send EXP? then STATUS."));
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (line.length()) {
        handleCommand(line);
        line = "";
      }
    } else if (line.length() < 150) {
      line += c;
    }
  }
  for (uint8_t axis = 0; axis < AXES; axis++) {
    if (axisEnabled[axis]) stepper[axis].run();
  }
}
