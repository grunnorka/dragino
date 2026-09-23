# PS-CB-NA PPK2 bench CLI

Agent-friendly CLI for powering, flashing, and verifying a Dragino **PS-CB-NA**
over a Nordic **PPK2** (source meter) plus FTDI USB-TTL with **RTS → Flash/ISP**.

Script: [`pscb_ppk2_cli.py`](pscb_ppk2_cli.py)

## Hardware

| Piece | Role |
|---|---|
| **PPK2** | Source meter (internal supply) → board VIN/GND. LED **red** when armed. |
| **FTDI USB-TTL** | Console + STM32 ROM ISP UART (`/dev/ttyUSB0` typical) |
| **RTS → Flash/ISP** | Empirically: `RTS=False` = ISP, `RTS=True` = Flash/normal |
| **Common GND** | PPK2 GND, FTDI GND, and board GND tied together |

PS-CB supply rating is roughly **2.6–3.6 V** (BG95 VBAT minimum is 3.3 V).
Default CLI voltage is **3600 mV**.

## Bench behaviour (measured 2026-09-23)

These three facts drive the whole design; ignore them and results lie.

1. **BOOT0 follows RTS.** A closed UART port releases RTS, which is the
   **ISP** level. If nothing holds `/dev/ttyUSB0` with RTS asserted at
   power-on, the board boots the **STM32 ROM bootloader** (answers `0x7F` with
   `0x79`), not the app. Every CLI command that powers the DUT therefore holds
   the console open at the Flash level (`--boot-mode flash`, the default).
2. **Closing the PPK2 port resets the PPK2.** The DTR drop makes it
   re-enumerate on USB (~1 s) and cuts DUT power (LED back to green). So each
   invocation cold-boots the board. `--hold-seconds` keeps it up while the CLI
   runs; `--keep-power` exits with DTR left up so the DUT **stays powered**
   after exit (and RTS stays at the Flash level so a reset still boots the app).
   The next command waits out a re-enumeration automatically.
3. **The app starts ~27.6 s after power-on.** Dragino bootloader v1.3 prints
   its banner, probes the modem (`AT` ×9), then stays silent until the app
   banner (`[BOOT-A]`, `Image Version:`). Boot checks wait up to
   `--settle-seconds` (default 45) and return as soon as the banner appears.

4. **A sleeping board can be phantom-powered by the FTDI.** In STOP mode the board draws
   <0.5 mA, and the FTDI's idle-high TX line feeds it through the MCU RX pin when the PPK2
   switches off: a "power cycle" then does nothing (uptime keeps counting). The CLI holds every
   UART it has open in **break** (TX low) while the DUT is off, and the off time is 3 s. A
   board left in STOP with the console *closed* can still be kept alive by the FTDI: unplug it
   (or keep a CLI process holding break) for a guaranteed cold start.

The PPK2 sends its calibration metadata only once per USB session. After a
`--keep-power` exit the next run loads it from
`~/.cache/pscb-ppk2/modifiers-<serial>.txt` (`current_summary.modifiers` says
`device`, `cache`, or `missing`).

## Setup

From the dragino repo root:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # includes ppk2-api, stm32loader, pyserial
```

Build the firmware hex in the openfw repo first:

```bash
cd ../ps-cb-openfw && make -j4
```

## Commands

| Command | What it does | `ok` means |
|---|---|---|
| `flash-run` | ISP power-cycle → flash hex → Flash-mode power-cycle → wait for app → `AT` | `AT` → `OK` |
| `flash` | Alias of `flash-run` | |
| `boot` | Flash-mode power-cycle → wait for app → `AT` | `AT` → `OK` |
| `cycle` | Power-cycle with the console held; waits for the app banner | banner seen |
| `on` | Power on (console held). Needs `--hold-seconds` or `--keep-power` | |
| `off` | DUT off | |
| `monitor` | Cold boot, then record current + console for `--seconds` from power-on | ran |

### Full flash + verify (main agent path)

```bash
cd /path/to/dragino
.venv/bin/python shared/pscb_ppk2_cli.py flash-run \
  --hex ../ps-cb-openfw/build/pscb-openfw.hex \
  --result logs/flash-result.json \
  --current-log logs/ppk2-current.jsonl
```

From the firmware repo:

```bash
make flash-ppk2
# → build/flash-ppk2-result.json, build/ppk2-current.jsonl, build/console.log
```

### Power measurement

```bash
.venv/bin/python shared/pscb_ppk2_cli.py monitor --seconds 120
```

### Leave the board running after the CLI exits

```bash
.venv/bin/python shared/pscb_ppk2_cli.py on --keep-power --hold-seconds 1
# ... other tools talk to the console ...
.venv/bin/python shared/pscb_ppk2_cli.py off
```

## Options agents care about

| Flag | Default | Meaning |
|---|---|---|
| `--ppk` | `auto` | PPK2 ACM port, or discover (waits up to 6 s for re-enumeration) |
| `--uart` | `$DRAGINO_PORT` or `/dev/ttyUSB0` | FTDI console/ISP |
| `--voltage-mv` | `3600` | Source voltage (warns above 3600) |
| `--off-seconds` | `3` | DUT off time during a cycle |
| `--settle-seconds` | `45` | Max wait for the app banner after power-on |
| `--hold-seconds` | `0` | Keep DUT ON (PPK2 + console held) after success |
| `--keep-power` | off | Leave DUT powered after exit (see above) |
| `--power-off` | off | DUT OFF before exit |
| `--boot-mode` | `flash` | `cycle`/`on`/`monitor`: RTS level held while powered |
| `--no-uart` | off | `cycle`/`on`/`monitor`: don't open the console (board then boots ISP unless RTS was left at Flash by `--keep-power`) |
| `--hex` | required for flash | App Intel HEX @ `0x08007800` |
| `--result` | auto under `logs/` | Full result JSON path |
| `--current-log` | auto under `logs/` | Current JSONL path |
| `--console-log` | auto under `logs/` | Timestamped console transcript |
| `--isp-rts` | `false` | RTS level for ISP (Flash uses inverse) |
| `--auto-rts` / `--no-auto-rts` | on | Retry inverted RTS if ISP sync fails |

## Results (machine-readable)

**Exit codes**

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Operational failure (ISP, flash, AT probe, …) |
| `2` | Bad args / missing deps |
| `3` | Hardware missing (no PPK2 / UART) |

**Stdout** always ends with a scrapeable line:

```text
RESULT_JSON {"ok": true, "command": "flash-run", "steps": [...], ...}
```

**`--result` JSON** (real `flash-run`, trimmed):

```json
{
  "ok": true,
  "command": "flash-run",
  "voltage_mv": 3600,
  "steps": [
    {"name": "arm", "ok": true},
    {"name": "isp_sync", "ok": true, "detail": {"isp_rts": false}},
    {"name": "flash", "ok": true, "detail": {"bytes": 41812, "address": "0x08007800", "write_s": 42.8}},
    {"name": "boot", "ok": true, "detail": {"bootloader_seen_s": 0.41, "app_banner_s": 27.56,
      "at_ok": true, "at_response": "OK", "image_version": "openfw-0.2.0", "console_tail": "…"}}
  ],
  "current_summary": {
    "capture_rate_sps": 99945, "modifiers": "device", "ppk_serial": "F87919B8E244",
    "total": {"avg_uA": 8147.31, "seconds": 85.14, "charge_uC": 693665.8, "energy_mJ": 2497.197},
    "phases": {
      "boot_on": {"avg_uA": 8773.32, "seconds": 27.575, "energy_mJ": 870.918, "max_uA": 1351434.8},
      "app":     {"avg_uA": 9949.14, "seconds": 1.604,  "energy_mJ": 57.433}
    }
  },
  "error": null
}
```

`current_summary.phases` gives avg/min/max current, seconds, charge and energy
per phase over the **full 100 kS/s** stream. Phases: `armed_off`, `isp_*`,
`flash_isp_*`, `flash_write`, `boot_off`, `boot_on` (power-on → app banner,
i.e. the bootloader), `app`, `hold`, `cycle_*`.

**`--current-log` JSONL**: one record per 50 ms window, never straddling a phase:

```json
{"ts": 1790180313.07, "t": 12.35, "uA": 11307.256, "n": 5000, "min_uA": 9917.7, "max_uA": 12607.2, "phase": "app"}
```

**`--console-log`**: every console line with seconds since power-on.

## Sequence details (`flash-run`)

1. Open PPK2, load calibration, source-meter mode, set voltage, start the
   sampler (DUT off).
2. Hold UART `RTS=ISP`, DUT power-cycle, sync STM32 ROM bootloader (`0x7F` / ACK).
3. Power-cycle into ISP again and flash app pages only (`≥ 0x08007800`) via
   `pscb_app_isp.flash_image` (bootloader preserved, every chunk read back).
4. Hold the console at `RTS=Flash`, DUT power-cycle, wait for the bootloader
   and app banners, then `AT` → `OK`.
5. Write result JSON; exit (power drops unless `--keep-power`).

Wired ISP popups / SW1 are **not** used.

## Troubleshooting

| Symptom | Check |
|---|---|
| `app banner not seen` | Raise `--settle-seconds`; check `console_tail` in the result |
| `AT` fails but banner seen | Console UART wiring; look at `--console-log` |
| Board answers `0x7F` instead of `AT` | It booted ISP: RTS was released at power-on (`--no-uart`?) |
| `No PPK2 found` | Re-enumeration took >6 s, or USB cable; `lsusb | grep 1915` |
| `modifiers: missing` | Run any command once without `--keep-power` to refresh the cache |
| ISP sync fails | RTS wiring; try `--isp-rts true` or leave `--auto-rts`; common GND |
| Flash refuses hex | Image must start at exactly `0x08007800` |

## Related

- Thin power-only helper (closes port on exit): [`ppk2_power_cycle.py`](ppk2_power_cycle.py)
- App-only ISP flash helpers: `PS-CB-NA/scripts/pscb_app_isp.py`
- Stock bootloader+app recover (prompts): `PS-CB-NA/scripts/recover_pscb.py`
- Firmware `make flash` (prompts) vs `make flash-ppk2` (this CLI)
