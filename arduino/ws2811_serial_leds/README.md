# WS2811 Serial LED Controller

Arduino sketch and Python helper for setting each of 16 WS2811/NeoPixel LEDs
from a text file.

## Wiring

- WS2811 data input to Arduino `D11`
- LED strip power to a matching external `5 V` or `12 V` supply
- LED supply ground to Arduino `GND`

Keep the Arduino ground and LED power supply ground connected together.

## Upload

```bash
sudo ~/bin/arduino-cli compile --fqbn arduino:avr:uno --upload -p /dev/ttyACM0 arduino/ws2811_serial_leds
```

The sketch needs the `Adafruit_NeoPixel` Arduino library.

## Color File

Edit `led_colors.txt`. Use one LED per line. Blank lines and comments are
ignored. Put inline comments after a space, like `red # LED 0`.

Supported formats:

```text
255,0,0
red
#00ff00
0 255 0
5 purple
6 #ff0078
7 20,20,20
```

If a line starts with an LED index, that color is assigned to that specific
LED. Otherwise, colors are assigned in order from LED `0` through LED `15`.

Named colors are `off`, `black`, `red`, `orange`, `yellow`, `green`, `cyan`,
`blue`, `purple`, `pink`, and `white`.

## Send Colors

```bash
python3 arduino/ws2811_serial_leds/led_write.py --port /dev/ttyACM0
python3 arduino/ws2811_serial_leds/led_write.py --port /dev/ttyACM0 --file arduino/ws2811_serial_leds/led_colors.txt
```

You can also send serial commands manually at `115200` baud:

```text
set 0 255 0 0
frame 255 0 0 0 255 0 0 0 255 ...48 total color numbers...
clear
help
```
