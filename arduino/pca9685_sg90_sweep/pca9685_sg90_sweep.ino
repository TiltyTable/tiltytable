#include <Wire.h>

const uint8_t PCA9685_ADDR = 0x40;
const uint8_t SERVO_CHANNEL = 0;

const float PWM_FREQ_HZ = 50.0;
const uint16_t SERVO_MIN_US = 500;
const uint16_t SERVO_MAX_US = 2400;

const uint8_t MODE1 = 0x00;
const uint8_t PRESCALE = 0xFE;
const uint8_t LED0_ON_L = 0x06;

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

void setPWMFreq(float freqHz) {
  uint8_t prescale = (uint8_t)((25000000.0 / (4096.0 * freqHz)) - 1.0 + 0.5);

  uint8_t oldMode = read8(MODE1);
  write8(MODE1, (oldMode & 0x7F) | 0x10);
  write8(PRESCALE, prescale);
  write8(MODE1, oldMode);
  delay(5);
  write8(MODE1, oldMode | 0xA1);
}

void setPWM(uint8_t channel, uint16_t offTick) {
  uint8_t reg = LED0_ON_L + 4 * channel;

  Wire.beginTransmission(PCA9685_ADDR);
  Wire.write(reg);
  Wire.write(0);
  Wire.write(0);
  Wire.write(offTick & 0xFF);
  Wire.write(offTick >> 8);
  Wire.endTransmission();
}

void writeServoAngle(uint8_t channel, int degrees) {
  uint16_t pulseUs = map(degrees, 0, 180, SERVO_MIN_US, SERVO_MAX_US);
  uint16_t ticks = (uint16_t)(pulseUs * 4096.0 / (1000000.0 / PWM_FREQ_HZ) + 0.5);
  setPWM(channel, ticks);
}

void setup() {
  Wire.begin();

  write8(MODE1, 0x00);
  delay(10);
  setPWMFreq(PWM_FREQ_HZ);
}

void loop() {
  for (int angle = 30; angle <= 150; angle++) {
    writeServoAngle(SERVO_CHANNEL, angle);
    delay(20);
  }

  for (int angle = 150; angle >= 30; angle--) {
    writeServoAngle(SERVO_CHANNEL, angle);
    delay(20);
  }
}
