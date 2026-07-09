#include <Adafruit_NeoPixel.h>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

// Wiring (WS2812 / WS2812B / WS2811 strip):
// - LED data input  -> Arduino D4
// - LED 5 V power    -> external 5 V supply (do not power 50 LEDs from the Arduino)
// - LED supply GND   -> Arduino GND (must share ground with the Arduino)
// A 300-470 ohm resistor on the data line and a large cap across the supply are recommended.
constexpr uint8_t LED_PIN = 4;
constexpr uint16_t LED_COUNT = 47;
constexpr uint8_t BRIGHTNESS = 255;
constexpr uint32_t SERIAL_BAUD = 115200;

// This strip is RGB-ordered (WS2811-style): sending "red" showed as green with
// NEO_GRB, so we use NEO_RGB. Flip back to NEO_GRB if colors look swapped.
Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_RGB + NEO_KHZ800);

// A full "frame" line is "frame " + LED_COUNT * "255 255 255 ", so reserve
// enough room to hold every value for the whole strip without truncating.
constexpr uint16_t LINE_MAX = (uint16_t)(LED_COUNT) * 12 + 16;
constexpr int MAX_TOKENS = (int)LED_COUNT * 3 + 2;

// Fixed buffers (no String/heap) keep RAM use predictable on an Uno.
char lineBuffer[LINE_MAX];
uint16_t lineLength = 0;
char *tokens[MAX_TOKENS];

bool parseByte(const char *text, uint8_t &value) {
  if (text == NULL || *text == '\0') {
    return false;
  }

  char *end = NULL;
  long parsed = strtol(text, &end, 10);
  if (*end != '\0' || parsed < 0 || parsed > 255) {
    return false;
  }

  value = (uint8_t)parsed;
  return true;
}

bool parseLedIndex(const char *text, uint16_t &value) {
  if (text == NULL || *text == '\0') {
    return false;
  }

  char *end = NULL;
  long parsed = strtol(text, &end, 10);
  if (*end != '\0' || parsed < 0 || parsed >= LED_COUNT) {
    return false;
  }

  value = (uint16_t)parsed;
  return true;
}

int splitTokens(char *input, char *out[], int maxTokens) {
  int count = 0;
  char *token = strtok(input, " \t,\r\n");

  while (token != NULL && count < maxTokens) {
    out[count++] = token;
    token = strtok(NULL, " \t,\r\n");
  }

  return count;
}

void printHelp() {
  Serial.println(F("WS2812/WS2811 NeoPixel serial LED controller"));
  Serial.print(F("LEDs: "));
  Serial.print(LED_COUNT);
  Serial.print(F("  pin: D"));
  Serial.println(LED_PIN);
  Serial.println(F("Commands:"));
  Serial.print(F("  set <led 0-"));
  Serial.print(LED_COUNT - 1);
  Serial.println(F("> <r> <g> <b>"));
  Serial.print(F("  frame <r0> <g0> <b0> ... ("));
  Serial.print(LED_COUNT * 3);
  Serial.println(F(" numbers)"));
  Serial.println(F("  clear | off"));
  Serial.println(F("  help"));
}

void handleSet(char *args[], int count) {
  if (count != 5) {
    Serial.println(F("ERR usage: set <led> <r> <g> <b>"));
    return;
  }

  uint16_t led = 0;
  uint8_t r = 0;
  uint8_t g = 0;
  uint8_t b = 0;

  if (!parseLedIndex(args[1], led)) {
    Serial.print(F("ERR led index must be 0-"));
    Serial.println(LED_COUNT - 1);
    return;
  }
  if (!parseByte(args[2], r) || !parseByte(args[3], g) || !parseByte(args[4], b)) {
    Serial.println(F("ERR colors must be 0-255"));
    return;
  }

  strip.setPixelColor(led, strip.Color(r, g, b));
  strip.show();

  Serial.print(F("OK led "));
  Serial.println(led);
}

void handleFrame(char *args[], int count) {
  const int expectedCount = 1 + (int)LED_COUNT * 3;
  if (count != expectedCount) {
    Serial.print(F("ERR frame needs "));
    Serial.print(LED_COUNT * 3);
    Serial.println(F(" color numbers"));
    return;
  }

  for (uint16_t led = 0; led < LED_COUNT; led++) {
    uint8_t r = 0;
    uint8_t g = 0;
    uint8_t b = 0;
    const int offset = 1 + led * 3;

    if (!parseByte(args[offset], r) || !parseByte(args[offset + 1], g) || !parseByte(args[offset + 2], b)) {
      Serial.println(F("ERR colors must be 0-255"));
      return;
    }

    strip.setPixelColor(led, strip.Color(r, g, b));
  }

  strip.show();
  Serial.println(F("OK frame"));
}

void processLine() {
  if (lineLength == 0) {
    return;
  }
  lineBuffer[lineLength] = '\0';

  int count = splitTokens(lineBuffer, tokens, MAX_TOKENS);
  if (count == 0) {
    return;
  }

  for (char *p = tokens[0]; *p; p++) {
    *p = tolower(*p);
  }

  if (strcmp(tokens[0], "set") == 0) {
    handleSet(tokens, count);
  } else if (strcmp(tokens[0], "frame") == 0) {
    handleFrame(tokens, count);
  } else if (strcmp(tokens[0], "clear") == 0 || strcmp(tokens[0], "off") == 0) {
    strip.clear();
    strip.show();
    Serial.println(F("OK clear"));
  } else if (strcmp(tokens[0], "help") == 0) {
    printHelp();
  } else {
    Serial.println(F("ERR unknown command"));
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);

  strip.begin();
  strip.setBrightness(BRIGHTNESS);
  strip.clear();
  strip.show();

  Serial.println(F("READY WS2812 LED controller"));
  printHelp();
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n') {
      processLine();
      lineLength = 0;
    } else if (c != '\r') {
      if (lineLength < LINE_MAX - 1) {
        lineBuffer[lineLength++] = c;
      }
    }
  }
}
