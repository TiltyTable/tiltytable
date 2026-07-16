# Archived Stewart control stack

This directory contains the retired direct-serial Stewart implementation. It
was archived on 2026-07-15 because these programs open the Arduino themselves,
use the older firmware/geometry protocol, and therefore cannot safely share
state with the permanently running `stewart_supervisor.py` process.

The archive includes:

- `python/`: direct-serial calibration, CLI, trackball, balancing, hold, and
  sweep programs.
- `arduino/uim5756pm_stewart/`: the older firmware-hosted IK executor.
- `tests/`: tests specific to the retired roller-ball implementation.

These files are retained for history only. Do not run them against a platform
owned by the supervisor: opening the Uno serial device can reset the board and
invalidate the live absolute-step state.

The supported stack lives at repository root and uses:

- `stewart_supervisor.py` and `stewart_supervisor_client.py`
- `arduino/uim5756pm_stewart_exp/`
- `analysis/stewart_exp_kinematics.py`
- `stewart_platform_control_position.py`
- `stewart_platform_control_velocity.py`

Every supported live connection obtains the supervisor's current `s0/s1/s2`
motor coordinates before it plans or sends a target.
