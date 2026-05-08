#include <Adafruit_NeoPixel.h>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

// Wiring:
// - WS2811 data input -> Arduino D11
// - LED strip 5 V / 12 V power -> matching external supply
// - LED supply ground -> Arduino GND
constexpr uint8_t LED_PIN = 11;
constexpr uint16_t LED_COUNT = 16;
constexpr uint8_t BRIGHTNESS = 255;
constexpr uint32_t SERIAL_BAUD = 115200;

Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_RGB + NEO_KHZ800);

String line;

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

int splitTokens(char *input, char *tokens[], int maxTokens) {
  int count = 0;
  char *token = strtok(input, " \t,\r\n");

  while (token != NULL && count < maxTokens) {
    tokens[count++] = token;
    token = strtok(NULL, " \t,\r\n");
  }

  return count;
}

void printHelp() {
  Serial.println(F("WS2811/NeoPixel serial LED controller"));
  Serial.println(F("Commands:"));
  Serial.println(F("  set <led> <r> <g> <b>"));
  Serial.println(F("  frame <r0> <g0> <b0> ... <r15> <g15> <b15>"));
  Serial.println(F("  clear"));
  Serial.println(F("  help"));
}

void handleSet(char *tokens[], int count) {
  if (count != 5) {
    Serial.println(F("ERR usage: set <led> <r> <g> <b>"));
    return;
  }

  uint16_t led = 0;
  uint8_t r = 0;
  uint8_t g = 0;
  uint8_t b = 0;

  if (!parseLedIndex(tokens[1], led)) {
    Serial.println(F("ERR led index must be 0-15"));
    return;
  }
  if (!parseByte(tokens[2], r) || !parseByte(tokens[3], g) || !parseByte(tokens[4], b)) {
    Serial.println(F("ERR colors must be 0-255"));
    return;
  }

  strip.setPixelColor(led, strip.Color(r, g, b));
  strip.show();

  Serial.print(F("OK led "));
  Serial.println(led);
}

void handleFrame(char *tokens[], int count) {
  const int expectedCount = 1 + LED_COUNT * 3;
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

    if (!parseByte(tokens[offset], r) || !parseByte(tokens[offset + 1], g) || !parseByte(tokens[offset + 2], b)) {
      Serial.println(F("ERR colors must be 0-255"));
      return;
    }

    strip.setPixelColor(led, strip.Color(r, g, b));
  }

  strip.show();
  Serial.println(F("OK frame"));
}

void handleLine(String input) {
  input.trim();
  if (input.length() == 0) {
    return;
  }

  char buffer[260];
  input.toCharArray(buffer, sizeof(buffer));

  char *tokens[50];
  int count = splitTokens(buffer, tokens, 50);
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

  Serial.println(F("READY WS2811 LED controller"));
  printHelp();
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n') {
      handleLine(line);
      line = "";
    } else if (c != '\r' && line.length() < 255) {
      line += c;
    }
  }
}
