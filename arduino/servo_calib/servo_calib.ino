/*
 * servo_calib.ino
 *
 * Firmware for an Arduino Uno R4 (standard board; 32KB RAM, Renesas RA4M1,
 * NOT AVR — see the freeMemory()/M-command note below) + Adafruit PCA9685
 * driving up to 16 SG90
 * servos (servo-actuated linear actuators needing RECESSED / NEUTRAL /
 * EXTENDED positions), PLUS 3 WS2812B/NeoPixel addressable LED strips
 * for wiring/power sanity checks.
 *
 * "Dumb" executor: drives servo pulse widths in MICROSECONDS and LED
 * strip colors, and acks every command. ALL calibration data lives on
 * the host (servo_tool.py + the JSON config) — nothing is stored here.
 *
 * Wiring:
 *   PCA9685 SDA → A4, SCL → A5,  GND ↔ Uno GND (signal reference).
 *   PCA9685 OE  → GND  (outputs permanently enabled — no software control).
 *   PCA9685 VCC → 5 V (logic).
 *   PCA9685 V+  → dedicated 5–6 V servo supply via the SCREW TERMINAL, and
 *                 the supply's (–) MUST return to the screw-terminal GND so
 *                 amps of servo current do NOT flow back through the Arduino.
 *
 *   LED strip A data → D2   (D1 was avoided — hardware Serial TX, collides
 *                            with the servo_tool.py serial link; A and B
 *                            were later swapped from their first wiring)
 *   LED strip B data → D6
 *   LED strip C data → D5
 *   (D3 and D6 briefly looked "bad" while chasing a strip that wouldn't
 *   light, but every pin drives a strip fine in isolation — the actual
 *   cause was SRAM pressure from resizing several strips to a large
 *   pixel count at once. See MAX_LEDS_PER_STRIP below.)
 *   LED strips share the Uno's GND; if they draw meaningful current, feed
 *   their 5V from the same servo supply (not the Uno's 5V pin) and only
 *   tie grounds together.
 *
 * Why microseconds instead of raw counts:
 *   Host commands are in µs. The firmware converts µs → PWM count using the
 *   configured oscillator frequency and 50 Hz prescale, so calibration stays
 *   in ordinary hobby-servo units.
 *
 * At boot, all 16 channels are left off / limp, and all LED strips are off.
 * Host calibration should only energize the one channel being actively jogged.
 *
 * ---------------------------------------------------------------------
 * SERIAL PROTOCOL  (115200 baud, '\n'-terminated, case-insensitive)
 *
 *   A <addr>      Set PCA9685 I2C address (0x40-0x7F) and leave outputs off.
 *                   → "OK A 0x40"
 *   P <ch> <us>   Set channel <ch> (0-15) pulse width in microseconds.
 *                   → "OK P <ch> <us>"
 *   O <ch>        Off: release channel (no pulse, servo limp). → "OK O <ch>"
 *   G <ch>        Get last µs written.  → "VAL <ch> <us>"  (-1 = off)
 *   X             All servo channels off. → "OK X"
 *   L <s> <r> <g> <b>  Set every pixel on LED strip <s> (0-2) to RGB
 *                   (each 0-255). → "OK L <s> <r> <g> <b>"
 *   LX            All LED strips off.    → "OK LX"
 *   LN <s> <n>    Resize strip <s> to <n> pixels (clears it). Use this
 *                   once at the start of a session to match the real
 *                   physical pixel count of that strip (capped at
 *                   MAX_LEDS_PER_STRIP for Uno SRAM safety).
 *                   → "OK LN <s> <n>"
 *   LP <s> <i> <r> <g> <b>  Set ONE pixel <i> on strip <s> to RGB, leaving
 *                   every other pixel on that strip untouched.
 *                   → "OK LP <s> <i> <r> <g> <b>"
 *   E / D         Accepted no-ops (OE hardwired). → "OK E" / "OK D"
 *   M             Report free SRAM in bytes.       → "MEM <bytes>"
 *                   AVR-only (classic Uno/Nano/Mega); on non-AVR boards
 *                   like the Uno R4 (the current standard board, 32KB RAM
 *                   vs the classic Uno's ~2KB) this returns -1 since the
 *                   underlying trick doesn't exist there and RAM pressure
 *                   is far less likely to be the cause of a symptom anyway.
 *   ?             Print help.
 *   else          → "ERR <echo>"
 * ---------------------------------------------------------------------
 *
 * WATCHDOG (stall/stuck-on guard):
 *   Any line received from the host (even a no-op like "E") resets a
 *   watchdog timer. If WATCHDOG_TIMEOUT_MS elapses with no host activity
 *   at all — host script crashed, USB unplugged, laptop slept — every
 *   servo channel is force-released (limp) so nothing can be left driven
 *   against a mechanical limit indefinitely. Prints
 *   "WATCHDOG released all channels" once when it fires. Send anything
 *   to clear it and resume. servo_tool.py keeps this from ever firing
 *   during normal use by sending a background heartbeat.
 * ---------------------------------------------------------------------
 */

#include <Wire.h>
#include <Adafruit_NeoPixel.h>

static const uint8_t DEFAULT_I2C_ADDR = 0x40;

static const uint8_t  NUM_CHANNELS = 16;
static const uint16_t NEUTRAL_US   = 1500;     // servo center
static const uint16_t US_MAX       = 3000;     // hard clamp (well under 20ms)
static const uint16_t OFF_SENTINEL = 0xFFFF;   // marks "channel is limp"
static const uint32_t OSC_FREQ_HZ  = 27000000; // Adafruit empirical default
static const uint16_t PWM_FREQ_HZ  = 50;

static const uint8_t PCA9685_MODE1      = 0x00;
static const uint8_t PCA9685_MODE2      = 0x01;
static const uint8_t PCA9685_LED0_ON_L  = 0x06;
static const uint8_t PCA9685_PRESCALE   = 0xFE;
static const uint8_t MODE1_RESTART      = 0x80;
static const uint8_t MODE1_SLEEP        = 0x10;
static const uint8_t MODE1_AI           = 0x20;
static const uint8_t MODE2_OUTDRV       = 0x04;

// ---- LED strips (WS2812B/NeoPixel) ----------------------------------
static const uint8_t  NUM_STRIPS          = 3;
// Pin note: D3 and D6 both looked "bad" during earlier testing, but that
// was a false lead — every pin (D2, D5, D6, and originally D3) drives a
// strip fine when tested ALONE. The actual cause was almost certainly
// SRAM pressure from resizing multiple strips to a large pixel count at
// the same time (see MAX_LEDS_PER_STRIP note below) corrupting one
// strip's state, not a damaged pin. Current physical wiring:
static const uint8_t  LED_PIN_0           = 2;   // strip A
static const uint8_t  LED_PIN_1           = 6;   // strip B
static const uint8_t  LED_PIN_2           = 5;   // strip C
static const uint16_t NUM_LEDS_PER_STRIP  = 8;    // initial/default length; send "LN <s> <n>" to resize
// Standard board is now the Uno R4 (32KB RAM, not the classic Uno's ~2KB),
// so this is a generous sanity ceiling against a typo'd "LN" value, not a
// tight budget like it was on the R3. 1000px x 3 strips x 3B/px = ~9KB,
// comfortably inside 32KB alongside everything else.
static const uint16_t MAX_LEDS_PER_STRIP  = 1000;

// Color order fixed 2026-07: these strips are physically RGB-ordered, not
// the more common GRB. Confirmed via `led_strip_test.py --color 0 255 0`
// (pure green) coming out pure RED on every strip — a clean R/G swap and
// nothing else (a GRB-vs-RGB byte-order mismatch swaps only the R and G
// wire positions; B is sent in the same position either way, which is
// also why this never showed up on WHITE, where R and G are equal).
Adafruit_NeoPixel stripA(NUM_LEDS_PER_STRIP, LED_PIN_0, NEO_RGB + NEO_KHZ800);
Adafruit_NeoPixel stripB(NUM_LEDS_PER_STRIP, LED_PIN_1, NEO_RGB + NEO_KHZ800);
Adafruit_NeoPixel stripC(NUM_LEDS_PER_STRIP, LED_PIN_2, NEO_RGB + NEO_KHZ800);
Adafruit_NeoPixel *ledStrips[NUM_STRIPS] = { &stripA, &stripB, &stripC };

// ---- stall/stuck-on watchdog -----------------------------------------
static const uint32_t WATCHDOG_TIMEOUT_MS = 5000;  // no host activity -> release all
uint32_t lastHostMillis = 0;
bool     watchdogFired  = false;

uint8_t  i2cAddr = DEFAULT_I2C_ADDR;
uint16_t curUs[NUM_CHANNELS];
bool     anyChannelEnergized = false;

char    lineBuf[48];
uint8_t lineLen = 0;

#if defined(__AVR__)
int freeMemory() {
    extern int __heap_start, *__brkval;
    int v;
    return (int)&v - (__brkval == 0 ? (int)&__heap_start : (int)__brkval);
}
#else
// __brkval/__heap_start are AVR-libc internals (classic Uno/Nano/Mega).
// On non-AVR cores (Uno R4's Renesas RA4M1, SAMD, ESP32, etc.) they don't
// exist, and those boards have far more RAM anyway (tens of KB, not ~2KB),
// so the SRAM-exhaustion concern this command exists for is much less
// likely to apply. Report -1 rather than failing to link.
int freeMemory() {
    return -1;
}
#endif

void printHexAddr(uint8_t addr) {
    Serial.print(F("0x"));
    if (addr < 0x10) Serial.print('0');
    Serial.print(addr, HEX);
}

bool write8(uint8_t reg, uint8_t val) {
    Wire.beginTransmission(i2cAddr);
    Wire.write(reg);
    Wire.write(val);
    return Wire.endTransmission() == 0;
}

bool read8(uint8_t reg, uint8_t &val) {
    Wire.beginTransmission(i2cAddr);
    Wire.write(reg);
    if (Wire.endTransmission() != 0) return false;
    if (Wire.requestFrom((int)i2cAddr, 1) != 1) return false;
    val = Wire.read();
    return true;
}

uint8_t servoPwmPrescale() {
    const uint32_t denom = (uint32_t)PWM_FREQ_HZ * 4096UL;
    return (uint8_t)(((OSC_FREQ_HZ + denom / 2) / denom) - 1);
}

bool setPwmRaw(uint8_t ch, uint16_t on, uint16_t off) {
    if (ch >= NUM_CHANNELS) return false;
    Wire.beginTransmission(i2cAddr);
    Wire.write(PCA9685_LED0_ON_L + 4 * ch);
    Wire.write(on & 0xFF);
    Wire.write(on >> 8);
    Wire.write(off & 0xFF);
    Wire.write(off >> 8);
    return Wire.endTransmission() == 0;
}

void updateEnergizedFlag() {
    anyChannelEnergized = false;
    for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
        if (curUs[i] != OFF_SENTINEL) { anyChannelEnergized = true; break; }
    }
}

void channelOff(uint8_t ch) {
    if (ch >= NUM_CHANNELS) return;
    curUs[ch] = OFF_SENTINEL;
    setPwmRaw(ch, 0, 4096);   // PCA9685 "full off" bit -> no pulse
    updateEnergizedFlag();
}

void channelSet(uint8_t ch, uint16_t us) {
    if (ch >= NUM_CHANNELS) return;
    if (us > US_MAX) us = US_MAX;
    curUs[ch] = us;
    uint32_t ticks = ((uint32_t)us * PWM_FREQ_HZ * 4096UL + 500000UL) / 1000000UL;
    if (ticks > 4095) ticks = 4095;
    setPwmRaw(ch, 0, (uint16_t)ticks);
    updateEnergizedFlag();
}

void releaseAllChannels() {
    for (uint8_t i = 0; i < NUM_CHANNELS; i++) channelOff(i);
}

bool configurePwm(uint8_t addr) {
    i2cAddr = addr;

    Wire.beginTransmission(i2cAddr);
    if (Wire.endTransmission() != 0) {
        Serial.print(F("ERR A NOACK "));
        printHexAddr(i2cAddr);
        Serial.println();
        return false;
    }

    if (!write8(PCA9685_MODE1, MODE1_RESTART)) {
        Serial.print(F("ERR A WRITE MODE1 "));
        printHexAddr(i2cAddr);
        Serial.println();
        return false;
    }
    delay(10);
    if (!write8(PCA9685_MODE2, MODE2_OUTDRV)) {
        Serial.print(F("ERR A WRITE MODE2 "));
        printHexAddr(i2cAddr);
        Serial.println();
        return false;
    }

    uint8_t oldmode;
    if (!read8(PCA9685_MODE1, oldmode)) {
        Serial.print(F("ERR A READ "));
        printHexAddr(i2cAddr);
        Serial.println();
        return false;
    }

    const uint8_t prescale = servoPwmPrescale();
    if (!write8(PCA9685_MODE1, (oldmode & ~MODE1_RESTART) | MODE1_SLEEP) ||
        !write8(PCA9685_PRESCALE, prescale) ||
        !write8(PCA9685_MODE1, oldmode | MODE1_AI)) {
        Serial.print(F("ERR A CONFIG "));
        printHexAddr(i2cAddr);
        Serial.println();
        return false;
    }
    delay(5);
    if (!write8(PCA9685_MODE1, (oldmode | MODE1_AI | MODE1_RESTART))) {
        Serial.print(F("ERR A RESTART "));
        printHexAddr(i2cAddr);
        Serial.println();
        return false;
    }

    uint8_t actualPrescale = 0xFF;
    if (!read8(PCA9685_PRESCALE, actualPrescale) || actualPrescale != prescale) {
        Serial.print(F("ERR A PRESCALE "));
        printHexAddr(i2cAddr);
        Serial.print(F(" expected "));
        Serial.print(prescale);
        Serial.print(F(" got "));
        Serial.println(actualPrescale);
        return false;
    }

    for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
        channelOff(i);
    }

    Serial.print(F("OK A "));
    printHexAddr(i2cAddr);
    Serial.println();
    return true;
}

// ---- LED helpers -----------------------------------------------------
void setLedColor(uint8_t idx, uint8_t r, uint8_t g, uint8_t b) {
    if (idx >= NUM_STRIPS) return;
    Adafruit_NeoPixel *s = ledStrips[idx];
    uint32_t c = s->Color(r, g, b);
    for (uint16_t i = 0; i < s->numPixels(); i++) s->setPixelColor(i, c);
    s->show();
}

void allLedsOff() {
    for (uint8_t i = 0; i < NUM_STRIPS; i++) setLedColor(i, 0, 0, 0);
}

void setOnePixel(uint8_t idx, uint16_t pixel, uint8_t r, uint8_t g, uint8_t b) {
    if (idx >= NUM_STRIPS) return;
    Adafruit_NeoPixel *s = ledStrips[idx];
    if (pixel >= s->numPixels()) return;   // silently ignore out-of-range (safe no-op)
    s->setPixelColor(pixel, s->Color(r, g, b));
    s->show();
}

void resizeStrip(uint8_t idx, uint16_t count) {
    if (idx >= NUM_STRIPS) return;
    if (count > MAX_LEDS_PER_STRIP) count = MAX_LEDS_PER_STRIP;
    ledStrips[idx]->updateLength(count);
    ledStrips[idx]->clear();
    ledStrips[idx]->show();
}

void printHelp() {
    Serial.println(F("PCA9685 + LED firmware. Commands (\\n-terminated):"));
    Serial.println(F("  A <addr>          set PCA9685 I2C address, e.g. A 0x40 or A 64"));
    Serial.println(F("  P <ch> <us>       set pulse width in microseconds"));
    Serial.println(F("  O <ch>            channel off (limp)"));
    Serial.println(F("  G <ch>            get last us (VAL ...; -1 = off)"));
    Serial.println(F("  X                 all servo channels off"));
    Serial.println(F("  L <s> <r> <g> <b> set LED strip s (0-2) to RGB (0-255 each)"));
    Serial.println(F("  LX                all LED strips off"));
    Serial.println(F("  LN <s> <n>        resize strip s to n pixels (clears it)"));
    Serial.println(F("  LP <s> <i> <r> <g> <b>  set one pixel i on strip s"));
    Serial.println(F("  E / D             no-ops (OE hardwired to GND)"));
    Serial.println(F("  M                 report free SRAM bytes"));
    Serial.println(F("  ?                 this help"));
}

void handleLine(char *line) {
    while (*line == ' ') line++;
    switch (*line) {
        case 'A': case 'a': {
            char *endp;
            long addr = strtol(line + 1, &endp, 0);
            if (endp != line + 1 && addr >= 0x40 && addr <= 0x7F) {
                configurePwm((uint8_t)addr);
            } else { Serial.print(F("ERR ")); Serial.println(line); }
            return;
        }
        case 'P': case 'p': {
            int ch, us;
            if (sscanf(line + 1, "%d %d", &ch, &us) == 2 &&
                ch >= 0 && ch < NUM_CHANNELS && us >= 0 && us <= US_MAX) {
                channelSet((uint8_t)ch, (uint16_t)us);
                Serial.print(F("OK P ")); Serial.print(ch);
                Serial.print(' ');        Serial.println(us);
            } else { Serial.print(F("ERR ")); Serial.println(line); }
            return;
        }
        case 'O': case 'o': {
            int ch;
            if (sscanf(line + 1, "%d", &ch) == 1 && ch >= 0 && ch < NUM_CHANNELS) {
                channelOff((uint8_t)ch);
                Serial.print(F("OK O ")); Serial.println(ch);
            } else { Serial.print(F("ERR ")); Serial.println(line); }
            return;
        }
        case 'G': case 'g': {
            int ch;
            if (sscanf(line + 1, "%d", &ch) == 1 && ch >= 0 && ch < NUM_CHANNELS) {
                long v = (curUs[ch] == OFF_SENTINEL) ? -1L : (long)curUs[ch];
                Serial.print(F("VAL ")); Serial.print(ch);
                Serial.print(' ');       Serial.println(v);
            } else { Serial.print(F("ERR ")); Serial.println(line); }
            return;
        }
        case 'X': case 'x':
            releaseAllChannels();
            Serial.println(F("OK X")); return;
        case 'L': case 'l': {
            char *p = line + 1;
            while (*p == ' ') p++;
            if ((*p == 'X' || *p == 'x') && *(p + 1) == '\0') {
                allLedsOff();
                Serial.println(F("OK LX"));
                return;
            }
            if (*p == 'N' || *p == 'n') {
                int strip, count;
                if (sscanf(p + 1, "%d %d", &strip, &count) == 2 &&
                    strip >= 0 && strip < NUM_STRIPS && count >= 0) {
                    resizeStrip((uint8_t)strip, (uint16_t)count);
                    Serial.print(F("OK LN ")); Serial.print(strip);
                    Serial.print(' ');         Serial.println(ledStrips[strip]->numPixels());
                } else { Serial.print(F("ERR ")); Serial.println(line); }
                return;
            }
            if (*p == 'P' || *p == 'p') {
                int strip, pixel, r, g, b;
                if (sscanf(p + 1, "%d %d %d %d %d", &strip, &pixel, &r, &g, &b) == 5 &&
                    strip >= 0 && strip < NUM_STRIPS && pixel >= 0 &&
                    r >= 0 && r <= 255 && g >= 0 && g <= 255 && b >= 0 && b <= 255) {
                    setOnePixel((uint8_t)strip, (uint16_t)pixel, (uint8_t)r, (uint8_t)g, (uint8_t)b);
                    Serial.print(F("OK LP ")); Serial.print(strip);
                    Serial.print(' ');         Serial.print(pixel);
                    Serial.print(' ');         Serial.print(r);
                    Serial.print(' ');         Serial.print(g);
                    Serial.print(' ');         Serial.println(b);
                } else { Serial.print(F("ERR ")); Serial.println(line); }
                return;
            }
            int strip, r, g, b;
            if (sscanf(line + 1, "%d %d %d %d", &strip, &r, &g, &b) == 4 &&
                strip >= 0 && strip < NUM_STRIPS &&
                r >= 0 && r <= 255 && g >= 0 && g <= 255 && b >= 0 && b <= 255) {
                setLedColor((uint8_t)strip, (uint8_t)r, (uint8_t)g, (uint8_t)b);
                Serial.print(F("OK L ")); Serial.print(strip);
                Serial.print(' ');        Serial.print(r);
                Serial.print(' ');        Serial.print(g);
                Serial.print(' ');        Serial.println(b);
            } else { Serial.print(F("ERR ")); Serial.println(line); }
            return;
        }
        case 'E': case 'e': Serial.println(F("OK E")); return;  // no-op
        case 'D': case 'd': Serial.println(F("OK D")); return;  // no-op
        case 'M': case 'm':
            Serial.print(F("MEM ")); Serial.println(freeMemory());
            return;
        case '?': printHelp(); return;
        default: Serial.print(F("ERR ")); Serial.println(line); return;
    }
}

void setup() {
    Serial.begin(115200);
    Wire.begin();

    configurePwm(DEFAULT_I2C_ADDR);

    for (uint8_t i = 0; i < NUM_STRIPS; i++) {
        ledStrips[i]->begin();
        ledStrips[i]->show();   // all off
    }

    lastHostMillis = millis();

    Serial.println(F("READY servo_calib (microseconds, all off)"));
    printHelp();
}

void loop() {
    while (Serial.available() > 0) {
        char ch = (char)Serial.read();
        if (ch == '\n' || ch == '\r') {
            if (lineLen > 0) {
                lineBuf[lineLen] = '\0';
                lastHostMillis = millis();
                watchdogFired = false;
                handleLine(lineBuf);
                lineLen = 0;
            }
        } else if (lineLen < sizeof(lineBuf) - 1) {
            lineBuf[lineLen++] = ch;
        }
    }

    if (!watchdogFired && anyChannelEnergized &&
        (millis() - lastHostMillis) > WATCHDOG_TIMEOUT_MS) {
        releaseAllChannels();
        watchdogFired = true;
        Serial.println(F("WATCHDOG released all channels"));
    }
}
