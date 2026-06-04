# Lian Li HydroShift II macOS daemon

PyUSB daemon for controlling a **Lian Li HydroShift II wireless AIO** from macOS/Hackintosh.

This project started from Suraj's working `lianli_daemon_v2.py` prototype, which was reverse engineered and validated on Arch Linux from the USB/RF behavior used by [`sgtaziz/lian-li-linux`](https://github.com/sgtaziz/lian-li-linux). It now runs persistently on macOS as a boot-time LaunchDaemon.

## Current status

Working on Suraj's Hackintosh:

- Discovers Lian Li wireless TX/RX dongles.
- Discovers the bound HydroShift / WaterBlock2 AIO by master MAC.
- Engages wireless theme mode.
- Sends AIO params every keepalive tick.
- Sends fan PWM every keepalive tick.
- Controls pump RPM through the WaterBlock2 timer mapping.
- Runs a coolant-temperature-based quiet/balanced fan curve.
- Runs a coolant-temperature-based pump curve.
- Smooths and validates noisy coolant telemetry.
- Handles stale telemetry without sudden full-blast fan/pump spikes.
- Starts automatically at macOS boot via LaunchDaemon.

Current installed service:

```text
Label:       com.suraj.lianli-hydroshift
Plist:       /Library/LaunchDaemons/com.suraj.lianli-hydroshift.plist
Config:      ~/.config/lianli-hydroshift/config.json
Logs:        ~/Library/Logs/lianli-hydroshift/
Theme:       theme_index = 5
Scheduling:  ProcessType=Standard, Nice=-5
```

## Project layout

```text
lianli_hydroshift/daemon.py      Main daemon
config/config.example.json       Tunable fan/pump curve and safety settings
launchd/*.plist.template         LaunchDaemon / LaunchAgent templates
scripts/install-launchdaemon.sh  Install boot-time service
scripts/uninstall-launchdaemon.sh
scripts/install-launchagent.sh   Optional login-time service alternative
scripts/uninstall-launchagent.sh
scripts/load-test.sh             CPU load test + live daemon log tail
scripts/status.sh                launchd/process status helper
tests/test_daemon.py             Protocol/curve/unit tests
lianli_daemon_v2.original.py     Original working prototype copied from Downloads
MILESTONES.md                    Progress log and roadmap
```

## Local setup

```bash
cd ~/Projects/lianli-hydroshift-macos
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Generate a user config:

```bash
python -m lianli_hydroshift.daemon --write-default-config
```

This writes:

```text
~/.config/lianli-hydroshift/config.json
```

Run manually while testing:

```bash
.venv/bin/python -m lianli_hydroshift.daemon --config ~/.config/lianli-hydroshift/config.json
```

Stop with `Ctrl+C`.

## Current fan and pump curve

The current curve is tuned as a **quiet daily-driver** profile: soft at idle/desktop coolant temperatures, gradual under sustained load, and protective if coolant reaches the mid/high 40s.

Fan PWM values are raw `0..255`. The daemon enforces `min_pwm = 26` for the HydroShift II 10% minimum duty.

```text
Coolant  Fan PWM  Approx duty
26C      35       14%
30C      36       14%
32C      40       16%
34C      48       19%
36C      62       24%
38C      82       32%
40C      108      42%
42C      138      54%
44C      172      67%
46C      205      80%
48C      235      92%
50C      255      100%
```

Pump curve:

```text
Coolant  Pump target
28C      1800 rpm
34C      1800 rpm
38C      1900 rpm
42C      2200 rpm
46C      2600 rpm
48C      2900 rpm
50C      3200 rpm
```

The pump can also be configured as fixed RPM:

```json
"pump": {
  "mode": "constant",
  "rpm": 2000
}
```

HydroShift II Square / WaterBlock2 supported pump range is clamped to `1600..3200` RPM.

## Noise control and telemetry handling

The RX telemetry path can report bogus low coolant values under heavy CPU load. The daemon now treats coolant as a slow-moving signal and applies guard rails before feeding the fan curve:

- valid coolant range: `18..70C`
- sudden downward drop rejection: `coolant_reject_drop_c = 4.0`
- time-aware cooldown allowance: `coolant_reject_drop_extra_c_per_s = 0.04`
- smoothing alpha: `0.45`
- max smoothed drop per tick: `0.75C`
- max smoothed rise per tick: `2.0C`

To avoid audible step changes, final fan/pump commands are slew-limited:

```text
fan ramp up:    +4 PWM/tick
fan ramp down:  -3 PWM/tick
pump ramp up:   +75 rpm/tick
pump ramp down: -50 rpm/tick
```

Stale telemetry handling:

```text
soft stale after: 30s
soft stale floor: fan 60/255, pump 1950 rpm
hard stale after: 600s
hard failsafe:    fan 160/255, pump 2800 rpm
```

## Load testing / curve tuning

Run a bounded CPU load while watching the daemon log:

```bash
cd ~/Projects/lianli-hydroshift-macos
./scripts/load-test.sh
```

Defaults:

- 5 minutes
- live daemon log tail
- logical CPU count minus two workers

Leaving a couple of threads free keeps the PyUSB daemon responsive while still creating a heavy load.

Useful variants:

```bash
./scripts/load-test.sh --duration 600 --workers 8
./scripts/load-test.sh --duration 300 --workers $(sysctl -n hw.logicalcpu)  # worst-case all-core saturation
./scripts/load-test.sh --duration 180 --no-tail
```

Stop early with `Ctrl+C`; the script cleans up load workers and the log tail.

Watch for these log fields:

```text
coolant=<smoothed water temp> raw=<latest accepted raw reading> telemetry=<ok|soft_stale|hard_stale> fan_pwm=<0-255> pump_target=<rpm>
```

## LaunchDaemon / autostart

The current recommended install is the system LaunchDaemon. It starts at macOS boot before login and restarts if it crashes.

Install or refresh:

```bash
cd ~/Projects/lianli-hydroshift-macos
./scripts/install-launchdaemon.sh
```

Uninstall:

```bash
./scripts/uninstall-launchdaemon.sh
```

Status:

```bash
./scripts/status.sh
launchctl print system/com.suraj.lianli-hydroshift
```

Restart after config/code changes:

```bash
sudo launchctl kickstart -k system/com.suraj.lianli-hydroshift
```

Logs:

```text
~/Library/Logs/lianli-hydroshift/com.suraj.lianli-hydroshift.log
~/Library/Logs/lianli-hydroshift/com.suraj.lianli-hydroshift.err
```

Security note: the LaunchDaemon runs code from this project path. For a more locked-down production setup, copy the project to a root-owned location such as `/Library/Application Support/LianLiHydroShift` before installing the LaunchDaemon.

## Optional LaunchAgent

A LaunchAgent template/script also exists. It starts at user login rather than system boot. Use this only if you want login-item behavior instead of boot-time fan control.

```bash
./scripts/install-launchagent.sh
./scripts/uninstall-launchagent.sh
```

Use **either** LaunchDaemon or LaunchAgent, not both.

## Theme discovery

`theme_index` is configurable. Upstream clamps to 12, but this project defaults `theme_index_max` to 31 so we can probe higher values.

Current selected theme:

```text
theme_index = 5
```

Manual scan example:

```bash
.venv/bin/python -m lianli_hydroshift.daemon \
  --config ~/.config/lianli-hydroshift/config.json \
  --scan-themes 0 31 \
  --theme-dwell-s 4
```

Watch the LCD/pump display and note which indexes are valid/interesting.

## Validation

```bash
cd ~/Projects/lianli-hydroshift-macos
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile lianli_hydroshift/daemon.py scripts/render-plist.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
plutil -lint launchd/com.suraj.lianli-hydroshift.plist.template
```

Current test suite covers:

- WaterBlock2 pump timer mapping.
- AIO RF parameter offset/layout.
- master-MAC-filtered discovery.
- parser off-by-one regression.
- quiet/protective curve shape.
- coolant filtering/rejection.
- stale telemetry target behavior.
- fan/pump slew limiting.

## Notes from v2 cleanup

Implemented changes compared with the original prototype:

- Stable AIO command index instead of a rolling `seq` counter.
- Discovery filters records by the TX dongle's master MAC.
- Fixed the sliding record parser's last-offset off-by-one.
- Sends AIO params and fan PWM every keepalive tick.
- Keeps sending control packets through short RX telemetry gaps.
- Staged stale telemetry handling instead of immediate full-blast failsafe.
- Configurable pump curve or fixed RPM.
- Configurable `theme_index_max` for theme probing.
- Structured logging and cleaner USB release on shutdown.
- LaunchDaemon boot autostart with `ProcessType=Standard` and `Nice=-5`.

## Next likely work

See [`MILESTONES.md`](./MILESTONES.md) for the roadmap. Near-term candidates:

- Catalogue valid theme indexes.
- Add a small command helper for theme changes without editing JSON.
- Package/harden the daemon into a root-owned install location.
- Investigate OpenRGB integration for lighting control.
