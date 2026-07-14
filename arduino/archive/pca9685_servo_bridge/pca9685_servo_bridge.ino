#include <Wire.h>

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

const uint32_t SERIAL_BAUD = 115200;
const uint32_t I2C_CLOCK_HZ = 400000UL;
const uint8_t PCA9685_ADDRESS = 0x40;
const uint8_t PCA9685_OE_PIN = A3;
const uint8_t SERVO_CHANNELS = 16;
const float SERVO_PWM_HZ = 50.0f;

const uint8_t MODE1 = 0x00;
const uint8_t MODE2 = 0x01;
const uint8_t PRESCALE = 0xFE;
const uint8_t LED0_ON_L = 0x06;

const uint8_t MODE1_RESTART = 0x80;
const uint8_t MODE1_SLEEP = 0x10;
const uint8_t MODE1_AUTOINC = 0x20;
const uint8_t MODE2_OUTDRV = 0x04;

struct ServoConfig {
  uint16_t minUs;
  uint16_t maxUs;
  uint16_t currentUs;
  uint8_t homeDeg;
  bool invert;
  bool enabled;
};

ServoConfig servoConfigs[SERVO_CHANNELS];
char commandBuffer[96];
uint8_t commandLength = 0;

bool tokenEquals(const char* lhs, const char* rhs) {
  while (*lhs != '\0' && *rhs != '\0') {
    if (toupper(*lhs) != toupper(*rhs)) {
      return false;
    }
    ++lhs;
    ++rhs;
  }
  return *lhs == '\0' && *rhs == '\0';
}

bool parseLongValue(const char* token, long& value) {
  char* end = nullptr;
  value = strtol(token, &end, 10);
  return end != token && *end == '\0';
}

bool parseFloatValue(const char* token, float& value) {
  char* end = nullptr;
  value = static_cast<float>(strtod(token, &end));
  return end != token && *end == '\0';
}

bool parseChannel(const char* token, uint8_t& channel) {
  long value = 0;
  if (!parseLongValue(token, value)) {
    return false;
  }
  if (value < 0 || value >= SERVO_CHANNELS) {
    return false;
  }
  channel = static_cast<uint8_t>(value);
  return true;
}

float clampAngle(float angleDeg) {
  if (angleDeg < 0.0f) {
    return 0.0f;
  }
  if (angleDeg > 180.0f) {
    return 180.0f;
  }
  return angleDeg;
}

uint16_t clampPulse(uint16_t pulseUs, uint16_t minUs, uint16_t maxUs) {
  if (pulseUs < minUs) {
    return minUs;
  }
  if (pulseUs > maxUs) {
    return maxUs;
  }
  return pulseUs;
}

uint16_t angleToPulseUs(const ServoConfig& config, float angleDeg) {
  float constrained = clampAngle(angleDeg);
  if (config.invert) {
    constrained = 180.0f - constrained;
  }
  const float normalized = constrained / 180.0f;
  const float spanUs = static_cast<float>(config.maxUs - config.minUs);
  const float pulse = static_cast<float>(config.minUs) + (normalized * spanUs);
  return static_cast<uint16_t>(pulse + 0.5f);
}

void writeRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(PCA9685_ADDRESS);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission();
}

uint8_t readRegister(uint8_t reg) {
  Wire.beginTransmission(PCA9685_ADDRESS);
  Wire.write(reg);
  Wire.endTransmission();
  Wire.requestFrom(static_cast<int>(PCA9685_ADDRESS), 1);
  if (Wire.available()) {
    return Wire.read();
  }
  return 0;
}

void setPwm(uint8_t channel, uint16_t onCount, uint16_t offCount) {
  const uint8_t base = LED0_ON_L + (4 * channel);
  Wire.beginTransmission(PCA9685_ADDRESS);
  Wire.write(base);
  Wire.write(onCount & 0xFF);
  Wire.write((onCount >> 8) & 0x0F);
  Wire.write(offCount & 0xFF);
  Wire.write((offCount >> 8) & 0x1F);
  Wire.endTransmission();
}

void setChannelOff(uint8_t channel) {
  setPwm(channel, 0, 4096);
}

uint16_t microsecondsToCounts(uint16_t pulseUs) {
  const float counts = (static_cast<float>(pulseUs) * SERVO_PWM_HZ * 4096.0f) / 1000000.0f;
  if (counts < 0.0f) {
    return 0;
  }
  if (counts > 4095.0f) {
    return 4095;
  }
  return static_cast<uint16_t>(counts + 0.5f);
}

void setPwmFrequency(float frequencyHz) {
  float prescaleEstimate = 25000000.0f;
  prescaleEstimate /= 4096.0f;
  prescaleEstimate /= frequencyHz;
  prescaleEstimate -= 1.0f;

  const uint8_t prescale = static_cast<uint8_t>(prescaleEstimate + 0.5f);
  const uint8_t oldMode = readRegister(MODE1);
  const uint8_t sleepMode = static_cast<uint8_t>((oldMode & 0x7F) | MODE1_SLEEP);

  writeRegister(MODE1, sleepMode);
  writeRegister(PRESCALE, prescale);
  writeRegister(MODE1, oldMode);
  delay(5);
  writeRegister(MODE1, static_cast<uint8_t>(oldMode | MODE1_AUTOINC | MODE1_RESTART));
}

void initializePca9685() {
  writeRegister(MODE2, MODE2_OUTDRV);
  writeRegister(MODE1, MODE1_AUTOINC);
  delay(5);
  setPwmFrequency(SERVO_PWM_HZ);
}

void setServoPulse(uint8_t channel, uint16_t pulseUs) {
  servoConfigs[channel].currentUs = clampPulse(pulseUs, servoConfigs[channel].minUs, servoConfigs[channel].maxUs);
  servoConfigs[channel].enabled = true;
  setPwm(channel, 0, microsecondsToCounts(servoConfigs[channel].currentUs));
}

void setServoAngle(uint8_t channel, float angleDeg) {
  setServoPulse(channel, angleToPulseUs(servoConfigs[channel], angleDeg));
}

void enableChannel(uint8_t channel) {
  servoConfigs[channel].enabled = true;
  setPwm(channel, 0, microsecondsToCounts(servoConfigs[channel].currentUs));
}

void disableChannel(uint8_t channel) {
  servoConfigs[channel].enabled = false;
  setChannelOff(channel);
}

void initializeServoConfigs() {
  for (uint8_t channel = 0; channel < SERVO_CHANNELS; ++channel) {
    servoConfigs[channel].minUs = 500;
    servoConfigs[channel].maxUs = 2400;
    servoConfigs[channel].homeDeg = 90;
    servoConfigs[channel].invert = false;
    servoConfigs[channel].enabled = false;
    servoConfigs[channel].currentUs = angleToPulseUs(servoConfigs[channel], servoConfigs[channel].homeDeg);
  }
}

void disableAllChannels() {
  for (uint8_t channel = 0; channel < SERVO_CHANNELS; ++channel) {
    disableChannel(channel);
  }
}

void homeChannel(uint8_t channel) {
  setServoAngle(channel, static_cast<float>(servoConfigs[channel].homeDeg));
}

void homeAllChannels() {
  for (uint8_t channel = 0; channel < SERVO_CHANNELS; ++channel) {
    homeChannel(channel);
  }
}

void enableAllChannels() {
  for (uint8_t channel = 0; channel < SERVO_CHANNELS; ++channel) {
    enableChannel(channel);
  }
}

void printStatus() {
  Serial.println(F("STATUS_BEGIN"));
  for (uint8_t channel = 0; channel < SERVO_CHANNELS; ++channel) {
    Serial.print(F("STATUS "));
    Serial.print(channel);
    Serial.print(' ');
    Serial.print(servoConfigs[channel].enabled ? 1 : 0);
    Serial.print(' ');
    Serial.print(servoConfigs[channel].minUs);
    Serial.print(' ');
    Serial.print(servoConfigs[channel].maxUs);
    Serial.print(' ');
    Serial.print(servoConfigs[channel].homeDeg);
    Serial.print(' ');
    Serial.print(servoConfigs[channel].invert ? 1 : 0);
    Serial.print(' ');
    Serial.println(servoConfigs[channel].currentUs);
  }
  Serial.println(F("STATUS_END"));
}

void printHelp() {
  Serial.println(F("PING"));
  Serial.println(F("STATUS"));
  Serial.println(F("MOVE <channel> <angleDeg>"));
  Serial.println(F("PULSE <channel> <pulseUs>"));
  Serial.println(F("CAL <channel> <minUs> <maxUs> <homeDeg> <invert0or1>"));
  Serial.println(F("HOME <channel|ALL>"));
  Serial.println(F("ENABLE <channel|ALL>"));
  Serial.println(F("DISABLE <channel|ALL>"));
}

int tokenize(char* line, char** tokens, int maxTokens) {
  int count = 0;
  char* savePtr = nullptr;
  char* token = strtok_r(line, " \t", &savePtr);
  while (token != nullptr && count < maxTokens) {
    tokens[count++] = token;
    token = strtok_r(nullptr, " \t", &savePtr);
  }
  return count;
}

void handleMoveCommand(int tokenCount, char** tokens) {
  if (tokenCount != 3) {
    Serial.println(F("ERR usage: MOVE <channel> <angleDeg>"));
    return;
  }

  uint8_t channel = 0;
  float angleDeg = 0.0f;
  if (!parseChannel(tokens[1], channel)) {
    Serial.println(F("ERR invalid channel"));
    return;
  }
  if (!parseFloatValue(tokens[2], angleDeg)) {
    Serial.println(F("ERR invalid angle"));
    return;
  }
  if (angleDeg < 0.0f || angleDeg > 180.0f) {
    Serial.println(F("ERR angle must be between 0 and 180"));
    return;
  }

  setServoAngle(channel, angleDeg);
  Serial.println(F("OK MOVE"));
}

void handlePulseCommand(int tokenCount, char** tokens) {
  if (tokenCount != 3) {
    Serial.println(F("ERR usage: PULSE <channel> <pulseUs>"));
    return;
  }

  uint8_t channel = 0;
  long pulseUs = 0;
  if (!parseChannel(tokens[1], channel)) {
    Serial.println(F("ERR invalid channel"));
    return;
  }
  if (!parseLongValue(tokens[2], pulseUs)) {
    Serial.println(F("ERR invalid pulse"));
    return;
  }
  if (pulseUs < 100 || pulseUs > 3000) {
    Serial.println(F("ERR pulse must be between 100 and 3000"));
    return;
  }

  setServoPulse(channel, static_cast<uint16_t>(pulseUs));
  Serial.println(F("OK PULSE"));
}

void handleCalibrationCommand(int tokenCount, char** tokens) {
  if (tokenCount != 6) {
    Serial.println(F("ERR usage: CAL <channel> <minUs> <maxUs> <homeDeg> <invert0or1>"));
    return;
  }

  uint8_t channel = 0;
  long minUs = 0;
  long maxUs = 0;
  float homeDegInput = 0.0f;
  long invert = 0;

  if (!parseChannel(tokens[1], channel)) {
    Serial.println(F("ERR invalid channel"));
    return;
  }
  if (!parseLongValue(tokens[2], minUs) || !parseLongValue(tokens[3], maxUs)) {
    Serial.println(F("ERR invalid pulse limits"));
    return;
  }
  if (!parseFloatValue(tokens[4], homeDegInput)) {
    Serial.println(F("ERR invalid home angle"));
    return;
  }
  if (!parseLongValue(tokens[5], invert)) {
    Serial.println(F("ERR invalid invert flag"));
    return;
  }
  if (minUs < 100 || maxUs > 3000 || minUs >= maxUs) {
    Serial.println(F("ERR pulse limits must satisfy 100 <= min < max <= 3000"));
    return;
  }
  if (homeDegInput < 0.0f || homeDegInput > 180.0f) {
    Serial.println(F("ERR home angle must be between 0 and 180"));
    return;
  }

  servoConfigs[channel].minUs = static_cast<uint16_t>(minUs);
  servoConfigs[channel].maxUs = static_cast<uint16_t>(maxUs);
  servoConfigs[channel].homeDeg = static_cast<uint8_t>(homeDegInput + 0.5f);
  servoConfigs[channel].invert = (invert != 0);
  servoConfigs[channel].currentUs = clampPulse(
    servoConfigs[channel].currentUs,
    servoConfigs[channel].minUs,
    servoConfigs[channel].maxUs
  );

  if (servoConfigs[channel].enabled) {
    setServoPulse(channel, servoConfigs[channel].currentUs);
  }

  Serial.println(F("OK CAL"));
}

void handleHomeCommand(int tokenCount, char** tokens) {
  if (tokenCount != 2) {
    Serial.println(F("ERR usage: HOME <channel|ALL>"));
    return;
  }

  if (tokenEquals(tokens[1], "ALL")) {
    homeAllChannels();
    Serial.println(F("OK HOME"));
    return;
  }

  uint8_t channel = 0;
  if (!parseChannel(tokens[1], channel)) {
    Serial.println(F("ERR invalid channel"));
    return;
  }

  homeChannel(channel);
  Serial.println(F("OK HOME"));
}

void handleEnableDisableCommand(int tokenCount, char** tokens, bool enableState) {
  if (tokenCount != 2) {
    Serial.println(enableState ? F("ERR usage: ENABLE <channel|ALL>") : F("ERR usage: DISABLE <channel|ALL>"));
    return;
  }

  if (tokenEquals(tokens[1], "ALL")) {
    if (enableState) {
      enableAllChannels();
    } else {
      disableAllChannels();
    }
    Serial.println(enableState ? F("OK ENABLE") : F("OK DISABLE"));
    return;
  }

  uint8_t channel = 0;
  if (!parseChannel(tokens[1], channel)) {
    Serial.println(F("ERR invalid channel"));
    return;
  }

  if (enableState) {
    enableChannel(channel);
  } else {
    disableChannel(channel);
  }
  Serial.println(enableState ? F("OK ENABLE") : F("OK DISABLE"));
}

void handleCommand(char* line) {
  char* tokens[8];
  const int tokenCount = tokenize(line, tokens, 8);
  if (tokenCount == 0) {
    return;
  }

  if (tokenEquals(tokens[0], "PING")) {
    Serial.println(F("OK PONG"));
    return;
  }

  if (tokenEquals(tokens[0], "HELP")) {
    printHelp();
    Serial.println(F("OK HELP"));
    return;
  }

  if (tokenEquals(tokens[0], "STATUS")) {
    printStatus();
    Serial.println(F("OK STATUS"));
    return;
  }

  if (tokenEquals(tokens[0], "MOVE")) {
    handleMoveCommand(tokenCount, tokens);
    return;
  }

  if (tokenEquals(tokens[0], "PULSE")) {
    handlePulseCommand(tokenCount, tokens);
    return;
  }

  if (tokenEquals(tokens[0], "CAL")) {
    handleCalibrationCommand(tokenCount, tokens);
    return;
  }

  if (tokenEquals(tokens[0], "HOME")) {
    handleHomeCommand(tokenCount, tokens);
    return;
  }

  if (tokenEquals(tokens[0], "ENABLE")) {
    handleEnableDisableCommand(tokenCount, tokens, true);
    return;
  }

  if (tokenEquals(tokens[0], "DISABLE")) {
    handleEnableDisableCommand(tokenCount, tokens, false);
    return;
  }

  Serial.println(F("ERR unknown command"));
}

void serviceSerial() {
  while (Serial.available() > 0) {
    const char incoming = static_cast<char>(Serial.read());

    if (incoming == '\r') {
      continue;
    }

    if (incoming == '\n') {
      commandBuffer[commandLength] = '\0';
      handleCommand(commandBuffer);
      commandLength = 0;
      continue;
    }

    if (commandLength >= sizeof(commandBuffer) - 1) {
      commandLength = 0;
      Serial.println(F("ERR command too long"));
      continue;
    }

    commandBuffer[commandLength++] = incoming;
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  pinMode(PCA9685_OE_PIN, OUTPUT);
  digitalWrite(PCA9685_OE_PIN, LOW);
  Wire.begin();
  Wire.setClock(I2C_CLOCK_HZ);

  initializeServoConfigs();
  initializePca9685();
  disableAllChannels();

  delay(10);
  Serial.println(F("READY channels=16 addr=0x40"));
}

void loop() {
  serviceSerial();
}
