#include <Adafruit_NeoPixel.h>

// Wiring:
// - WS2811 data input -> Arduino D11
// - LED strip 5 V / 12 V power -> matching external supply
// - LED supply ground -> Arduino GND
constexpr uint8_t LED_PIN = 11;
constexpr uint16_t LED_COUNT = 16;
constexpr uint8_t BRIGHTNESS = 255;
constexpr uint16_t COLOR_HOLD_MS = 350;
constexpr uint16_t RAINBOW_STEP_MS = 20;

Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);

const uint32_t colors[] = {
  strip.Color(255, 0, 0),      // Red
  strip.Color(255, 80, 0),     // Orange
  strip.Color(255, 255, 0),    // Yellow
  strip.Color(0, 255, 0),      // Green
  strip.Color(0, 255, 255),    // Cyan
  strip.Color(0, 0, 255),      // Blue
  strip.Color(180, 0, 255),    // Purple
  strip.Color(255, 0, 120),    // Pink
  strip.Color(255, 255, 255),  // White
};

void fillAll(uint32_t color) {
  for (uint16_t i = 0; i < LED_COUNT; i++) {
    strip.setPixelColor(i, color);
  }

  strip.show();
}

void rainbowCycle() {
  for (uint16_t frame = 0; frame < 256; frame++) {
    for (uint16_t i = 0; i < LED_COUNT; i++) {
      const uint16_t hue = ((i * 65536UL / LED_COUNT) + (frame * 256UL)) & 0xFFFF;
      strip.setPixelColor(i, strip.ColorHSV(hue, 255, 255));
    }

    strip.show();
    delay(RAINBOW_STEP_MS);
  }
}

void setup() {
  strip.begin();
  strip.setBrightness(BRIGHTNESS);
  strip.clear();
  strip.show();
}

void loop() {
  for (uint8_t i = 0; i < sizeof(colors) / sizeof(colors[0]); i++) {
    fillAll(colors[i]);
    delay(COLOR_HOLD_MS);
  }

  rainbowCycle();
}
