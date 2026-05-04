# PCA9685 SG90 Serial Servo Controller

Arduino sketch for driving up to 16 SG90 180-degree servos through a PCA9685 PWM driver.
The Jetson talks to the Arduino over USB serial at `115200` baud.

## Wiring

- Arduino `5V` to PCA9685 `VCC`
- Arduino `GND` to PCA9685 `GND`
- Arduino `SDA` to PCA9685 `SDA`
- Arduino `SCL` to PCA9685 `SCL`
- Servo signal wires to PCA9685 channels `0` through `15`
- Servo power supply `+5V` to PCA9685 `V+`
- Servo power supply ground to PCA9685 `GND`

On an Uno/Nano, `SDA` is `A4` and `SCL` is `A5`.

Use an external 5V supply for the servo if it jitters, browns out, or has load on
it. Keep all grounds common: Jetson, Arduino, PCA9685, and servo supply.

## Serial Commands

Open the Arduino serial port at `115200` baud and send one command per line.

```text
<deg>         set all channel angles, 0-180
a <deg>       set all channel angles, 0-180
a <ch> <deg>  set channel angle, 0-180
u <us>        set all channel raw pulse widths
u <ch> <us>   set channel raw pulse width
min <us>      set angle 0 pulse width
max <us>      set angle 180 pulse width
off           disable all channel outputs
off <ch>      disable channel output
help          print command help
```

Examples:

```text
90
a 0
a 90
a 180
a 3 45
u 1500
```

The SG90 defaults are `500 us` for 0 degrees, `2400 us` for 180 degrees, and
`50 Hz` PWM.

## Jetson Example

Find the Arduino port:

```bash
ls /dev/ttyACM* /dev/ttyUSB*
```

Send a position with the included helper:

```bash
python3 arduino/pca9685_serial_servo/servo_write.py --port /dev/ttyACM0 90
python3 arduino/pca9685_serial_servo/servo_write.py --port /dev/ttyACM0 --channel 3 45
python3 arduino/pca9685_serial_servo/servo_write.py --port /dev/ttyACM0 --pulse-us 1500
```

Control one servo at a time from the keyboard:

```bash
python3 arduino/pca9685_serial_servo/servo_write.py --port /dev/ttyACM0 --interactive
```

In interactive mode, left/right selects the previous/next PCA9685 channel, and
up/down changes the angle in degrees. The terminal prints the selected channel
and current angle after each keypress. Use `q` to quit.

Or send a raw serial command:

```bash
stty -F /dev/ttyACM0 115200 raw -echo
printf 'a 90\n' > /dev/ttyACM0
```

If permissions fail, add your user to `dialout`, then log out and back in:

```bash
sudo usermod -a -G dialout "$USER"
```

For a quick temporary test without logging out:

```bash
sudo chmod a+rw /dev/ttyACM0
```

Upload to an Uno with:

```bash
sudo ~/bin/arduino-cli compile --fqbn arduino:avr:uno --upload -p /dev/ttyACM0 arduino/pca9685_serial_servo
```
