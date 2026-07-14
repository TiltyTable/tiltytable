# WS2812 Serial LED Controller

Arduino sketch plus Python helpers for driving a WS2812 / WS2812B / WS2811
addressable LED strip (default **50 LEDs**) over USB serial.

## Wiring

- LED data input to Arduino `D4`
- LED `5 V` power to an external `5 V` supply (do **not** power 50 LEDs from the
  Arduino's 5 V pin)
- LED supply ground to Arduino `GND`

Keep the Arduino ground and the LED supply ground connected together. A
`300-470` ohm resistor in series with the data line and a large capacitor
(`1000 uF`) across the supply are recommended.

## Upload

```bash
sudo ~/bin/arduino-cli compile --fqbn arduino:avr:uno --upload -p /dev/ttyACM0 arduino/ws2811_serial_leds
```

The sketch needs the `Adafruit_NeoPixel` Arduino library.

Strip size, data pin, and color order live at the top of
`ws2811_serial_leds.ino` (`LED_COUNT`, `LED_PIN`, and `NEO_GRB`). WS2812 strips
are usually `GRB`; if red and green look swapped, switch `NEO_GRB` to `NEO_RGB`
and re-upload. The `rgb` test below makes this easy to check.

## Test the strip (`led_test.py`)

`led_test.py` is the single test tool for the strip. Run it with no test name
to play a full self-test, or pass a test name to debug a specific thing.

```bash
# Full self-test: rgb check -> wipe -> chase -> rainbow -> blink -> off
python3 arduino/ws2811_serial_leds/led_test.py --port /dev/ttyACM0

# Individual tests
python3 arduino/ws2811_serial_leds/led_test.py --port /dev/ttyACM0 all red
python3 arduino/ws2811_serial_leds/led_test.py --port /dev/ttyACM0 index 17 cyan
python3 arduino/ws2811_serial_leds/led_test.py --port /dev/ttyACM0 rainbow
python3 arduino/ws2811_serial_leds/led_test.py --port /dev/ttyACM0 rgb     # verify color order
python3 arduino/ws2811_serial_leds/led_test.py --port /dev/ttyACM0 count   # count LEDs one by one
python3 arduino/ws2811_serial_leds/led_test.py --port /dev/ttyACM0 off

# Useful flags
--brightness 0-255   software brightness scaling (default 255)
--count N            number of LEDs, must match the sketch (default 50)
--delay SECONDS      animation step delay     --cycles N   animation repeats
-v                   print the Arduino's replies
```

Colors accept a name (`off`, `red`, `orange`, `yellow`, `green`, `cyan`,
`blue`, `purple`, `pink`, `white`), a hex value (`#ff0078`), or `r,g,b`.

## Send a saved frame (`led_write.py`)

`led_write.py` pushes a fixed picture from `led_colors.txt` to the strip:

```bash
python3 arduino/ws2811_serial_leds/led_write.py --port /dev/ttyACM0
python3 arduino/ws2811_serial_leds/led_write.py --port /dev/ttyACM0 --file arduino/ws2811_serial_leds/led_colors.txt
```

## Serial protocol

You can also drive the strip by hand at `115200` baud:

```text
set 0 255 0 0
frame <r0> <g0> <b0> ... 150 numbers (50 LEDs x 3)
clear
help
```
