# Stewart platform control analysis and changes

Date: 2026-07-15

## Outcome

The experimental inverse-kinematics equations close correctly, but the old
control path did not make `(roll=0, pitch=0)` a unique physical command.
Free heave, two crank branches per leg, path-dependent optimization, and large
persisted motor offsets could all cause a later logical zero to use different
motor coordinates from the starting zero. The new controllers use one shared
Arduino step executor, deterministic symmetric branch selection at calibration,
free-heave host IK, and zero motor offsets unless corrections are explicitly
provided on the command line.

## Analysis findings

### Experimental host IK

- `analysis/stewart_exp_kinematics.py` produces valid arm closure. A grid of
  13,845 feasible samples over roll, pitch, and heave had a worst numerical arm
  length error of approximately `4.3e-14 mm`.
- A roll/pitch target is intentionally non-unique because heave is free and
  every leg has two closure branches. The local optimizer uses the preceding
  solution, so the actuator result depends on the path taken to the endpoint.
- Existing tests documented that non-uniqueness but did not require a
  zero/non-zero/zero sequence to reproduce the original motor coordinates.
- Symmetric poses at the crank-up dead center had branch scores that differed
  only by floating-point roundoff. That could select different branches for
  otherwise identical legs. The solver now quantizes only the score's
  continuity terms before comparing branch combinations and uses the branch
  tuple as a deterministic final tie-break.

### Persisted tuning offsets

- The committed baseline of `calibration/stewart_game_tuning.json` contains motor offsets
  `[-8340, -5200, -5300]`, equivalent to approximately
  `[-187.65°, -117.0°, -119.25°]` at 16,000 steps per crank revolution.
- Those values are large enough to behave like absolute pose coordinates, not
  small differential corrections. Adding them to every IK result makes the
  physical effect depend strongly on the selected heave and branch.
- Under the checked geometry, applying those offsets to a nominal level pose
  produces forward-kinematic tilt that changes with heave. This is consistent
  with a platform that does not look level after returning to logical zero.
- The two new controllers therefore default to `(0, 0, 0)` step offsets and do
  not load the legacy tuning file. Corrections must be deliberately supplied
  with `--step-offsets S0 S1 S2`.

### Other code-path issues found and archived

- The legacy firmware's `solveCrankAngle()` clamps an out-of-range cosine
  into `[-1, 1]` rather than rejecting the unreachable triangle. The newer
  experimental host solver correctly rejects it. The new controllers do not
  use the production firmware IK.
- `ball_balancer.py` sends `pose ... heave=0`, while the current production
  geometry declares a minimum heave of 12 mm and its own tests show the level
  zero-heave pose exceeds the rod-end limit. That client also does not read the
  rejection, so a failed return-to-level command can be silent.
- Arduino step counters are open-loop pulse coordinates, not encoder feedback.
  Passing software tests cannot rule out missed steps, gearbox backlash, link
  compliance, or a calibration error on the physical machine.

## New control architecture

Both public Python programs use the same existing Arduino firmware:

```text
arduino/uim5756pm_stewart_exp/uim5756pm_stewart_exp.ino
```

That firmware remains a calibration-gated absolute-step executor. It does not
contain a second copy of IK. The shared Python layer owns trackball decoding,
roll/pitch targets, free-heave IK, branch continuity, serial commands, arming,
and hold-on-exit behavior.

The Python programs connect exclusively through `stewart_supervisor.py`, which
keeps a no-DTR serial owner alive. Opening an Uno directly may reset it,
invalidate the persisted position state, and require calibration, so the live
tools no longer expose a direct-serial fallback.

`ExpLink.open()` now performs `EXP?` followed by `STATUS` and retains that
startup snapshot. Every live motion client constructs its initial IK pose from
the returned `s0/s1/s2` coordinates (with any explicit step offsets applied)
before it plans or sends a target. The profile utility also reads and displays
`STATUS` before changing the motion profile.

## Files modified

### `analysis/stewart_exp_kinematics.py`

Made symmetric branch selection deterministic. This prevents tiny trigonometric
roundoff differences between leg azimuths from sending identical legs down
different branches when leaving the calibration dead center.

### `stewart_platform_control_common.py`

Added the common host layer used by both controllers:

- Linux `REL_X`/`REL_Y` parsing committed at `SYN_REPORT` boundaries.
- Input accumulation that retains early events until the next control tick.
- Experimental firmware identity checking through the existing `ExpLink`.
- Interactive calibration when firmware state is not already calibrated.
- Runtime crank speed/acceleration setup, arming, and final `HOLD`.
- A configurable startup heave transition so the table leaves max-heave dead
  center before tilt control.
- Canonical zero handling: an exact `(0, 0)` request settles back to the
  configured startup heave, while nonzero endpoints retain free-heave IK.
- Radially bounded roll/pitch targets and bounded IK waypoints.
- Free-heave IK with continuity and the 12-degree crank target limit.
- Explicit optional motor step offsets, defaulting to zero.

### `stewart_platform_control_position.py`

Added direct roll/pitch position control:

- Positive X trackball counts increase pitch by default.
- Positive Y trackball counts increase roll by default.
- Counts accumulate into an absolute platform target.
- `--degrees-per-count`, axis signs, deadband, maximum tilt, and motion profile
  are configurable.

### `stewart_platform_control_velocity.py`

Added inertial angular-velocity control:

- A positive X swipe adds positive pitch velocity by default.
- A positive Y swipe adds positive roll velocity by default.
- Velocity integrates into the absolute roll/pitch target.
- Velocity decays exponentially to zero using `--velocity-decay-s`.
- Outward radial velocity is removed at the maximum tilt boundary to prevent
  limit windup.
- Swipe gain, decay, speed limit, epsilon, signs, and deadband are configurable.

### `tests/test_stewart_platform_control.py`

Added tests for direct axis mapping, radial pose clamping, velocity impulse
mapping, exponential decay, outward-velocity removal, complete trackball frame
handling, and deterministic symmetric branch selection during the startup
heave transition.

### Supervisor startup-state enforcement

- `stewart_exp_probe.py`: made `ExpLink` supervisor-only and captured a parsed
  `startup_status` as part of every live connection.
- `stewart_exp_roller_ball.py`, `stewart_exp_tune.py`, and
  `stewart_platform_control_common.py`: initialize their current IK pose from
  that supervisor-owned snapshot.
- `stewart_exp_profile.py`: reads and prints `STATUS` before profile commands.
- `tests/test_stewart_supervisor.py`: verifies that opening an `ExpLink`
  captures nonzero signed motor positions without substituting zeroes.

### Archived legacy stack

Moved the direct-serial Python programs, their roller test, and the old
firmware-hosted IK sketch into `archive/stewart_legacy/`. These programs could
reset or compete with the permanent supervisor and used a different geometry
and protocol. `archive/stewart_legacy/README.md` records the boundary and the
risk. Root and firmware documentation now point to the supervisor-owned stack.

## Running the controllers

Start the persistent serial supervisor if it is not already running:

```bash
.venv/bin/python3 stewart_supervisor.py
```

Direct position control:

```bash
.venv/bin/python3 stewart_platform_control_position.py
```

Decaying angular-velocity control:

```bash
.venv/bin/python3 stewart_platform_control_velocity.py
```

Useful low-risk first tests:

```bash
.venv/bin/python3 stewart_platform_control_position.py \
  --max-tilt 2 --degrees-per-count 0.02

.venv/bin/python3 stewart_platform_control_velocity.py \
  --max-tilt 2 --velocity-per-count 0.2 --max-velocity 5
```

Use `--roll-sign -1` or `--pitch-sign -1` if a physical axis moves opposite
the desired convention.

## Validation

The implementation was checked with:

```bash
.venv/bin/python3 -m unittest discover -s tests -v
.venv/bin/python3 -m compileall -q -x '/camera/mvsdk.py$' .
arduino-cli compile --fqbn arduino:avr:uno \
  arduino/uim5756pm_stewart_exp
```

All 127 discovered unit tests reported `OK`, including the new supervisor
startup-state and zero/non-zero/zero regressions. The process then aborted
during interpreter teardown after the existing control-center test attempted
to probe unavailable `/dev/video0` OpenCV/V4L2 devices. The focused Stewart
suites exit cleanly, and Python byte-compilation plus the live Arduino build
exit successfully.

Hardware motion was not performed during this change. The first live run should
be mechanically supported, limited to 2 degrees, and checked for axis signs,
missed steps, branch clearance, and repeatable physical level before increasing
the envelope or speed.
