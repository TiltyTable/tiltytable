/*
  UIM5756PM one-motor UART configuration tool (Uno R4 WiFi)

  This dedicated sketch NEVER generates STEP pulses and forces all three
  Stewart ENA outputs inactive. Connect exactly one motor's configuration
  UART at a time:

    Motor white TX -> Uno A4 (software UART RX)
    Motor green RX <- Uno A5 (software UART TX)
    Motor black signal GND -> Uno GND

  Motor power (24-48 V) must be on, but its driver output must remain disabled.
  USB serial monitor: 115200 baud, newline enabled.

  Commands:
    get
    set 4 CONFIRM
    help

  Protocol derived from UIROBOT CFG344 v250730:
    UART 57600 8N1
    8-byte frame: AA CMD D0 D1 D2 D3 D4 CC
    query MCS = 0x02, set MCS = 0x82, save EEPROM = 0x8A
*/

#include <Arduino.h>
#include <SoftwareSerial.h>

const uint8_t CFG_RX_PIN = A4;  // Motor white TX -> Uno RX
const uint8_t CFG_TX_PIN = A5;  // Motor green RX <- Uno TX
const unsigned long CFG_BAUD = 57600;
const unsigned long REPLY_TIMEOUT_MS = 500;

// Same active-low Stewart enable wiring as the runtime firmware.
const uint8_t ENA_PIN[3] = {4, 9, 13};

const uint8_t FRAME_START = 0xAA;
const uint8_t FRAME_END = 0xCC;
const uint8_t CMD_GET_MCS = 0x02;
const uint8_t CMD_SET_MCS = 0x82;
const uint8_t CMD_SAVE_EEPROM = 0x8A;

SoftwareSerial motorUart(CFG_RX_PIN, CFG_TX_PIN);
String line;

bool validMcs(long value) {
  return value == 1 || value == 2 || value == 4 || value == 8 ||
         value == 16 || value == 32 || value == 64 || value == 128;
}

void disableAllDrivers() {
  for (uint8_t i = 0; i < 3; i++) {
    pinMode(ENA_PIN[i], OUTPUT);
    digitalWrite(ENA_PIN[i], HIGH);  // ENA active-low: HIGH = disabled
  }
}

void clearMotorRx() {
  while (motorUart.available()) motorUart.read();
}

void printHexByte(uint8_t value) {
  if (value < 0x10) Serial.print('0');
  Serial.print(value, HEX);
}

void printBytes(const __FlashStringHelper *label, const uint8_t *data, size_t length) {
  Serial.print(label);
  for (size_t i = 0; i < length; i++) {
    if (i) Serial.print(' ');
    printHexByte(data[i]);
  }
  Serial.println();
}

size_t exchangeFrame(
  uint8_t command,
  uint8_t data0,
  uint8_t *reply,
  size_t replyCapacity
) {
  uint8_t frame[8] = {
    FRAME_START, command, data0, 0x00, 0x00, 0x00, 0x00, FRAME_END
  };

  clearMotorRx();
  printBytes(F("TX: "), frame, sizeof(frame));
  motorUart.write(frame, sizeof(frame));
  motorUart.flush();

  size_t count = 0;
  unsigned long deadline = millis() + REPLY_TIMEOUT_MS;
  while ((long)(deadline - millis()) > 0 && count < replyCapacity) {
    if (motorUart.available()) {
      reply[count++] = (uint8_t)motorUart.read();
      // Continue briefly after each byte so a complete frame can arrive.
      deadline = millis() + 50;
    }
  }

  if (count) printBytes(F("RX: "), reply, count);
  else Serial.println(F("RX: <no reply>"));
  return count;
}

bool findReplyFrame(
  const uint8_t *reply,
  size_t length,
  uint8_t expectedCommand,
  uint8_t *data0
) {
  if (length < 8) return false;
  for (size_t i = 0; i + 7 < length; i++) {
    if (reply[i] == FRAME_START &&
        reply[i + 1] == expectedCommand &&
        reply[i + 7] == FRAME_END) {
      *data0 = reply[i + 2];
      return true;
    }
  }
  return false;
}

bool queryMcs(uint8_t *mcs) {
  uint8_t reply[32];
  size_t length = exchangeFrame(CMD_GET_MCS, 0, reply, sizeof(reply));
  if (!findReplyFrame(reply, length, CMD_GET_MCS, mcs)) {
    Serial.println(F("ERR no valid MCS reply (expect AA 02 <MCS> ... CC)"));
    return false;
  }
  Serial.print(F("OK MCS "));
  Serial.println(*mcs);
  return true;
}

void setMcs(uint8_t mcs) {
  uint8_t oldMcs = 0;
  Serial.println(F("Checking connected motor before write..."));
  if (!queryMcs(&oldMcs)) {
    Serial.println(F("ERR refusing write: verify TX/RX/GND and motor power"));
    return;
  }

  Serial.print(F("Writing MCS "));
  Serial.print(mcs);
  Serial.print(F(" (was "));
  Serial.print(oldMcs);
  Serial.println(F(")..."));

  uint8_t reply[32];
  exchangeFrame(CMD_SET_MCS, mcs, reply, sizeof(reply));
  delay(100);
  exchangeFrame(CMD_SAVE_EEPROM, 0, reply, sizeof(reply));

  Serial.println(F("OK write sequence sent"));
  Serial.println(F("Power-cycle the motor, reconnect UART, then run: get"));
}

void printHelp() {
  Serial.println(F("UIM5756PM CONFIG (one motor connected at a time)"));
  Serial.println(F("A4 RX <- white motor TX"));
  Serial.println(F("A5 TX -> green motor RX"));
  Serial.println(F("Commands:"));
  Serial.println(F("  get"));
  Serial.println(F("  set 4 CONFIRM   (or another supported MCS)"));
  Serial.println(F("  help"));
  Serial.println(F("All Stewart motor outputs are DISABLED; no STEP pulses are generated."));
}

void handleCommand(String command) {
  command.trim();
  if (!command.length()) return;

  if (command.equalsIgnoreCase("get")) {
    uint8_t mcs = 0;
    queryMcs(&mcs);
    return;
  }

  if (command.equalsIgnoreCase("help") || command == "?") {
    printHelp();
    return;
  }

  if (command.startsWith("set ")) {
    int firstSpace = command.indexOf(' ');
    int secondSpace = command.indexOf(' ', firstSpace + 1);
    if (secondSpace < 0) {
      Serial.println(F("ERR use: set <MCS> CONFIRM"));
      return;
    }
    long requested = command.substring(firstSpace + 1, secondSpace).toInt();
    String confirmation = command.substring(secondSpace + 1);
    confirmation.trim();
    if (!validMcs(requested)) {
      Serial.println(F("ERR MCS must be 1,2,4,8,16,32,64,128"));
      return;
    }
    if (!confirmation.equalsIgnoreCase("CONFIRM")) {
      Serial.println(F("ERR append CONFIRM to write EEPROM"));
      return;
    }
    setMcs((uint8_t)requested);
    return;
  }

  Serial.println(F("ERR unknown command; send: help"));
}

void setup() {
  disableAllDrivers();
  Serial.begin(115200);
  motorUart.begin(CFG_BAUD);
  delay(250);
  printHelp();
}

void loop() {
  // Reassert disabled outputs in case of electrical disturbance.
  disableAllDrivers();

  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (line.length()) {
        handleCommand(line);
        line = "";
      }
    } else if (line.length() < 63) {
      line += c;
    }
  }
}
