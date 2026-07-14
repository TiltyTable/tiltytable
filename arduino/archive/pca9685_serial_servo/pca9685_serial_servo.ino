#include <Wire.h>
#include <ctype.h>
#include <stdlib.h>

// PCA9685 servo controller at the common default I2C address.
// Change this if you have adjusted the PCA9685 address solder jumpers.
const uint8_t PCA9685_ADDR = 0x40;

// SG90 180-degree servo defaults. If your horn buzzes or binds at the ends,
// trim these inward with the serial "min" and "max" commands.
const float DEFAULT_FREQ_HZ = 50.0;
const uint16_t DEFAULT_MIN_US = 500;
const uint16_t DEFAULT_MAX_US = 2400;
const uint8_t SERVO_CHANNELS = 16;

const uint8_t MODE1 = 0x00;
const uint8_t PRESCALE = 0xFE;
const uint8_t LED0_ON_L = 0x06;

float pwmFreqHz = DEFAULT_FREQ_HZ;
uint16_t minPulseUs = DEFAULT_MIN_US;
uint16_t maxPulseUs = DEFAULT_MAX_US;

String line;

void write8(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(PCA9685_ADDR);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission();
}

uint8_t read8(uint8_t reg) {
  Wire.beginTransmission(PCA9685_ADDR);
  Wire.write(reg);
  Wire.endTransmission();
  Wire.requestFrom(PCA9685_ADDR, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0;
}

bool validChannel(int channel) {
  if (channel < 0 || channel > 15) {
    Serial.println(F("ERR channel must be 0-15"));
    return false;
  }
  return true;
}

bool setPWMFreq(float freqHz) {
  if (freqHz < 24.0 || freqHz > 1526.0) {
    Serial.println(F("ERR freq must be 24-1526 Hz"));
    return false;
  }

  pwmFreqHz = freqHz;

  // PCA9685 datasheet formula using the nominal 25 MHz oscillator.
  float prescaleValue = 25000000.0;
  prescaleValue /= 4096.0;
  prescaleValue /= freqHz;
  prescaleValue -= 1.0;
  uint8_t prescale = (uint8_t)(prescaleValue + 0.5);

  uint8_t oldMode = read8(MODE1);
  uint8_t sleepMode = (oldMode & 0x7F) | 0x10;
  write8(MODE1, sleepMode);
  write8(PRESCALE, prescale);
  write8(MODE1, oldMode);
  delay(5);
  write8(MODE1, oldMode | 0xA1);  // auto-increment + restart
  return true;
}

bool setPWM(int channel, uint16_t onTick, uint16_t offTick) {
  if (!validChannel(channel)) {
    return false;
  }

  uint8_t reg = LED0_ON_L + 4 * channel;
  Wire.beginTransmission(PCA9685_ADDR);
  Wire.write(reg);
  Wire.write(onTick & 0xFF);
  Wire.write(onTick >> 8);
  Wire.write(offTick & 0xFF);
  Wire.write(offTick >> 8);
  Wire.endTransmission();
  return true;
}

bool setPWMFullOff(int channel) {
  if (!validChannel(channel)) {
    return false;
  }

  uint8_t reg = LED0_ON_L + 4 * channel;
  Wire.beginTransmission(PCA9685_ADDR);
  Wire.write(reg);
  Wire.write((uint8_t)0);
  Wire.write((uint8_t)0);
  Wire.write((uint8_t)0);
  Wire.write((uint8_t)0x10);  // LEDn_OFF_H full-off bit.
  Wire.endTransmission();
  return true;
}

uint16_t microsecondsToTicks(uint16_t pulseUs) {
  float periodUs = 1000000.0 / pwmFreqHz;
  float ticks = pulseUs * 4096.0 / periodUs;
  if (ticks < 0) {
    return 0;
  }
  if (ticks > 4095) {
    return 4095;
  }
  return (uint16_t)(ticks + 0.5);
}

void writePulse(int channel, uint16_t pulseUs) {
  uint16_t ticks = microsecondsToTicks(pulseUs);
  if (!setPWM(channel, 0, ticks)) {
    return;
  }

  Serial.print(F("OK channel "));
  Serial.print(channel);
  Serial.print(F(" pulse_us "));
  Serial.print(pulseUs);
  Serial.print(F(" ticks "));
  Serial.println(ticks);
}

void writeAllPulses(uint16_t pulseUs) {
  uint16_t ticks = microsecondsToTicks(pulseUs);

  for (uint8_t channel = 0; channel < SERVO_CHANNELS; channel++) {
    if (!setPWM(channel, 0, ticks)) {
      return;
    }
  }

  Serial.print(F("OK all pulse_us "));
  Serial.print(pulseUs);
  Serial.print(F(" ticks "));
  Serial.println(ticks);
}

void writeAngle(int channel, float degrees) {
  if (degrees < 0.0) {
    degrees = 0.0;
  }
  if (degrees > 180.0) {
    degrees = 180.0;
  }

  uint16_t pulseUs = (uint16_t)(minPulseUs + (maxPulseUs - minPulseUs) * (degrees / 180.0) + 0.5);
  writePulse(channel, pulseUs);
}

void writeAllAngles(float degrees) {
  if (degrees < 0.0) {
    degrees = 0.0;
  }
  if (degrees > 180.0) {
    degrees = 180.0;
  }

  uint16_t pulseUs = (uint16_t)(minPulseUs + (maxPulseUs - minPulseUs) * (degrees / 180.0) + 0.5);
  writeAllPulses(pulseUs);
}

void turnOff(int channel) {
  if (!setPWMFullOff(channel)) {
    return;
  }
  Serial.print(F("OK channel "));
  Serial.print(channel);
  Serial.println(F(" off"));
}

void turnAllOff() {
  for (uint8_t channel = 0; channel < SERVO_CHANNELS; channel++) {
    if (!setPWMFullOff(channel)) {
      return;
    }
  }

  Serial.println(F("OK all off"));
}

void turnAllOffQuietly() {
  for (uint8_t channel = 0; channel < SERVO_CHANNELS; channel++) {
    setPWMFullOff(channel);
  }
}

void printHelp() {
  Serial.println(F("PCA9685 serial servo controller"));
  Serial.println(F("Commands:"));
  Serial.println(F("  <deg>                set all channels angle, 0-180"));
  Serial.println(F("  a <deg>              set all channels angle, 0-180"));
  Serial.println(F("  a <ch> <deg>         set channel angle, 0-180"));
  Serial.println(F("  u <us>               set all channels pulse width"));
  Serial.println(F("  u <ch> <us>          set channel pulse width"));
  Serial.println(F("  min <us>             set angle 0 pulse width"));
  Serial.println(F("  max <us>             set angle 180 pulse width"));
  Serial.println(F("  freq <hz>            set PCA9685 PWM frequency"));
  Serial.println(F("  p                    disable all channel outputs"));
  Serial.println(F("  off                  disable all channel outputs"));
  Serial.println(F("  off <ch>             disable channel output"));
  Serial.println(F("Examples: a 90, a 3 45, u 1500, u 3 1200, p"));
}

int splitTokens(char *input, char *tokens[], int maxTokens) {
  int count = 0;
  char *token = strtok(input, " \t\r\n");

  while (token != NULL && count < maxTokens) {
    tokens[count++] = token;
    token = strtok(NULL, " \t\r\n");
  }

  return count;
}

bool isNumberToken(const char *token) {
  bool sawDigit = false;

  if (*token == '-' || *token == '+') {
    token++;
  }

  while (*token != '\0') {
    if (isdigit(*token)) {
      sawDigit = true;
    } else if (*token != '.') {
      return false;
    }
    token++;
  }

  return sawDigit;
}

void handleCommand(String command) {
  command.trim();
  if (command.length() == 0) {
    return;
  }

  char buffer[64];
  command.toCharArray(buffer, sizeof(buffer));

  char *tokens[3];
  int count = splitTokens(buffer, tokens, 3);
  if (count == 0) {
    return;
  }

  String cmd = tokens[0];
  cmd.toLowerCase();

  if (count == 1 && isNumberToken(tokens[0])) {
    writeAllAngles(atof(tokens[0]));
    return;
  }

  if (cmd == "help" || cmd == "?") {
    printHelp();
    return;
  }

  if (cmd == "a" || cmd == "angle") {
    if (count == 2) {
      writeAllAngles(atof(tokens[1]));
      return;
    }
    if (count == 3) {
      writeAngle(atoi(tokens[1]), atof(tokens[2]));
      return;
    }
    Serial.println(F("ERR usage: a <deg> or a <ch> <deg>"));
    return;
  }

  if (cmd == "u" || cmd == "us" || cmd == "pulse") {
    if (count == 2) {
      writeAllPulses((uint16_t)atoi(tokens[1]));
      return;
    }
    if (count == 3) {
      writePulse(atoi(tokens[1]), (uint16_t)atoi(tokens[2]));
      return;
    }
    Serial.println(F("ERR usage: u <us> or u <ch> <us>"));
    return;
  }

  if (cmd == "min") {
    if (count != 2) {
      Serial.println(F("ERR usage: min <us>"));
      return;
    }
    uint16_t value = (uint16_t)atoi(tokens[1]);
    if (value >= maxPulseUs) {
      Serial.println(F("ERR min must be less than max"));
      return;
    }
    minPulseUs = value;
    Serial.print(F("OK min_us "));
    Serial.println(minPulseUs);
    return;
  }

  if (cmd == "max") {
    if (count != 2) {
      Serial.println(F("ERR usage: max <us>"));
      return;
    }
    uint16_t value = (uint16_t)atoi(tokens[1]);
    if (value <= minPulseUs) {
      Serial.println(F("ERR max must be greater than min"));
      return;
    }
    maxPulseUs = value;
    Serial.print(F("OK max_us "));
    Serial.println(maxPulseUs);
    return;
  }

  if (cmd == "freq") {
    if (count != 2) {
      Serial.println(F("ERR usage: freq <hz>"));
      return;
    }
    if (!setPWMFreq(atof(tokens[1]))) {
      return;
    }
    Serial.print(F("OK freq_hz "));
    Serial.println(pwmFreqHz);
    return;
  }

  if (cmd == "p" || cmd == "pause" || cmd == "off") {
    if (count == 1) {
      turnAllOff();
      return;
    }
    if (cmd == "p" || cmd == "pause") {
      Serial.println(F("ERR usage: p"));
      return;
    }
    if (count == 2) {
      turnOff(atoi(tokens[1]));
      return;
    }
    Serial.println(F("ERR usage: off or off <ch>"));
    return;
  }

  Serial.println(F("ERR unknown command; type help"));
}

void setup() {
  Serial.begin(115200);
  unsigned long serialStartMs = millis();
  while (!Serial && millis() - serialStartMs < 2000) {
    delay(10);
  }

  Wire.begin();

  write8(MODE1, 0x00);
  delay(10);
  setPWMFreq(DEFAULT_FREQ_HZ);
  turnAllOffQuietly();

  Serial.println(F("READY"));
  printHelp();
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();

    if (c == '\n' || c == '\r') {
      handleCommand(line);
      line = "";
    } else if (line.length() < 63) {
      line += c;
    }
  }
}
