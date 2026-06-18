# Recovery runbook

Operator guide for diagnosing and recovering the Lian Li HydroShift II wireless
setup on macOS/Hackintosh. The goal is to handle a recurrence from these
docs/scripts without reconstructing packet details from logs or upstream source.

## First step, always: run the doctor

```bash
cd ~/Projects/lianli-hydroshift-macos
./scripts/doctor.sh
```

It is read-only (no RF writes, does not take the dongles) and prints a `STATUS:`
plus a suggested `ACTION:`. Match the status below.

## Healthy state

```text
== Lian Li HydroShift doctor ==
  USB TX dongle : ok
  USB RX dongle : ok
  LCD direct USB: present
  Daemon process: running
  Telemetry     : ok (last seen 0s ago)
  Discovery     : aio_bound
  OpenRGB bridge: reachable (127.0.0.1:6743)
  OpenRGB app   : running
  Last RGB frame: <n>s ago

  STATUS: HEALTHY
  ACTION: All checks passed.
```

Nothing to do.

## STATUS: DISCONNECTED — a dongle is missing

Symptom: `USB TX dongle` or `USB RX dongle` shows `MISSING`.

1. Reseat/replug the USB dongle.
2. Re-run `./scripts/doctor.sh`.
3. If still missing after a replug, the dongle or port is the problem — try a
   different USB port.

## STATUS: DAEMON_DOWN — hardware present, daemon not running

```bash
sudo launchctl kickstart -k system/com.suraj.lianli-hydroshift
./scripts/doctor.sh
```

If it will not stay up, check the log:

```bash
tail -n 100 ~/Library/Logs/lianli-hydroshift/com.suraj.lianli-hydroshift.err
```

## STATUS: UNBOUND — AIO visible but unbound (the 2026-06-18 failure mode)

Symptom: the doctor shows `Discovery: aio_unbound`, and the daemon log contains:

```text
AIO visible but unbound (mac=2d:a3:74:e5:66:e1 master=00:00:00:00:00:00 ch=8 rx_type=254 device_type=11)
```

The RX can see the HydroShift AIO record, but the AIO advertises an all-zero
master, so the daemon's normal bound-device discovery skips it.

### Bind recovery

1. Diagnose without sending anything (stops the daemon to free the dongles, but
   sends no RF writes):

   ```bash
   ./scripts/recover-display.sh --dry-run
   ```

   Confirm it reports the unbound AIO and the intended bind/SaveConfig plan.

2. Apply the recovery:

   ```bash
   ./scripts/recover-display.sh
   ```

   It binds the AIO to the local master, waits for convergence, broadcasts
   SaveConfig, then sends the wireless-theme switch burst.

3. Restart the daemon:

   ```bash
   sudo launchctl kickstart -k system/com.suraj.lianli-hydroshift
   ./scripts/doctor.sh
   ```

### Optional: daemon auto-rebind

The daemon can self-heal this exact case if you opt in. In
`~/.config/lianli-hydroshift/config.json`:

```json
"auto_rebind_visible_aio": true,
"auto_rebind_allow_list": ["2d:a3:74:e5:66:e1"]
```

It only auto-rebinds when exactly one unbound AIO is visible (and, if the
allow-list is non-empty, only that MAC). Default is off; when off, the daemon
logs the manual `recover-display.sh` command instead.

## STATUS: STALE — no fresh telemetry

The daemon is running but telemetry has not updated recently. Usually a transient
USB/libusb hiccup; the daemon reopens the dongles on repeated failures.

```bash
tail -n 100 ~/Library/Logs/lianli-hydroshift/com.suraj.lianli-hydroshift.err
# if it persists:
sudo launchctl kickstart -k system/com.suraj.lianli-hydroshift
```

## STATUS: RGB_NOT_APPLIED — cooling healthy, RGB out of sync

Cooling/telemetry are fine but the OpenRGB bridge is closed or no RGB frame has
been applied. The daemon re-sends its last cached RGB frame automatically on
reconnect; this is the escalation when OpenRGB itself was restarted.

```bash
./scripts/reapply-openrgb-profile.sh
./scripts/doctor.sh
```

## When to stop and inspect hardware

Stop running software recovery and inspect/replug the hardware (or pursue an RMA)
when:

- A dongle still shows `MISSING` after replug and trying another port.
- `recover-display.sh` reports the AIO but binding never converges across
  repeated runs.
- The **LCD display itself is corrupted** (Lian Li logo + hardware-temp overlay
  instead of the wireless theme) — this is a separate, more serious failure.

> ⚠️ The LCD corruption from theme index 13+ is **not** software-recoverable on
> any OS — it is persisted to the LCD controller flash and bricks the controller.
> `theme_index_max` is hard-clamped to 12 to prevent recurrence; do not raise it.
> See the "Display recovery incident (2026-06-04)" and RMA sections in
> [`../MILESTONES.md`](../MILESTONES.md) for the full history.

## Healthy-state verification checklist

After any recovery, confirm:

- daemon entering the control loop (`entering control loop` in the log)
- `telemetry=ok` in recent log lines
- fan/pump RPM telemetry present
- OpenRGB profile matched `wireless:<aio-mac>` (`OpenRGB profile matched device`)
- daemon logging `applied OpenRGB RGB frame: <n> LEDs`
- `./scripts/doctor.sh` reports `STATUS: HEALTHY`
