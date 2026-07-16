# UIM5756PM one-motor MCS configurator

Dedicated Uno R4 WiFi firmware for changing one connected UIM5756PM motor's MCS
without Windows. It disables all Stewart ENA outputs and never generates STEP
pulses. The experimental stack currently targets **MCS=4**; production still
targets MCS=8.

Protocol was derived from the official UIROBOT CFG344 v250730 executable:
57600 baud, 8N1, 8-byte `AA CMD D0 D1 D2 D3 D4 CC` frames.

## Wiring

Connect exactly one motor's UART cable at a time:

| Motor wire | Uno R4 WiFi |
| --- | --- |
| White TX | A4 (software UART RX) |
| Green RX | A5 (software UART TX) |
| Black signal GND | GND |

The motor remains powered from its normal 24–48 V supply. Do not connect motor
power to the Uno. Mechanically support the table before upload, motor power
cycling, or unplugging any controller.

## Upload

```bash
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi arduino/uim5756pm_config
arduino-cli upload -p /dev/arduino-stewart \
  --fqbn arduino:renesas_uno:unor4wifi arduino/uim5756pm_config
arduino-cli monitor -p /dev/arduino-stewart -c baudrate=115200
```

Use newline termination in the monitor:

```text
get
set 4 CONFIRM
```

`set` first requires a valid MCS query response, then sends the MCS write and
EEPROM-save frames. After configuring all three motors one at a time:

1. Power-cycle the motor supply.
2. Reconnect each motor UART and run `get`; each must report the selected MCS.
3. Flash firmware whose `STEPS_PER_CRANK_REV` matches that MCS.
4. Recalibrate all three cranks before motion.

Never run Stewart motion firmware while motors have mixed MCS settings.
