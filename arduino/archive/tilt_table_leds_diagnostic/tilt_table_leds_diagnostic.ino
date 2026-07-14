#include <Adafruit_NeoPixel.h>

constexpr uint16_t LED_COUNT = 16;
constexpr uint8_t BRIGHTNESS = 90;
constexpr uint16_t PIN_HOLD_MS = 1400;

const uint8_t testPins[] = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13};

void blinkStatus(uint8_t count) {
  for (uint8_t i = 0; i < count; i++) {
    digitalWrite(LED_BUILTIN, HIGH);
    delay(120);
    digitalWrite(LED_BUILTIN, LOW);
    delay(120);
  }
}

void fillStrip(Adafruit_NeoPixel &strip, uint32_t color) {
  for (uint16_t i = 0; i < LED_COUNT; i++) {
    strip.setPixelColor(i, color);
  }
  strip.show();
}

void runPinTest(uint8_t pin) {
  Adafruit_NeoPixel strip(LED_COUNT, pin, NEO_GRB + NEO_KHZ800);

  strip.begin();
  strip.setBrightness(BRIGHTNESS);
  strip.clear();
  strip.show();

  blinkStatus(2);
  fillStrip(strip, strip.Color(255, 0, 0));
  delay(PIN_HOLD_MS);
  fillStrip(strip, strip.Color(0, 255, 0));
  delay(PIN_HOLD_MS);
  fillStrip(strip, strip.Color(0, 0, 255));
  delay(PIN_HOLD_MS);
  strip.clear();
  strip.show();
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  blinkStatus(6);
}

void loop() {
  for (uint8_t i = 0; i < sizeof(testPins); i++) {
    runPinTest(testPins[i]);
  }
}
