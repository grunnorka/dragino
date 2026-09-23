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

Do **not** close the PPK2 serial port while the board must stay powered — source
output usually drops (LED back to green) when the host disconnects.

PS-CB supply rating is roughly **2.6–3.6 V**. Default CLI voltage is **3700 mV**
(matches a LiPo-ish bench setting); pass `--voltage-mv 3300` for the manual max.

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

| Command | What it does |
|---|---|
| `flash-run` | Arm PPK2 → ISP power-cycle → flash hex → Flash-mode power-cycle → `AT` probe |
| `flash` | Alias of `flash-run` |
| `boot` | Arm → Flash-mode power-cycle → banner/`AT` probe |
| `cycle` | Arm → DUT off/on |
| `on` / `off` | Arm then DUT ON or OFF |
| `monitor` | Arm and sample current for `--seconds` |

### Full flash + verify (main agent path)

```bash
cd /path/to/dragino
.venv/bin/python shared/pscb_ppk2_cli.py flash-run \
  --hex ../ps-cb-openfw/build/pscb-openfw.hex \
  --result logs/flash-result.json \
  --current-log logs/ppk2-current.jsonl \
  --voltage-mv 3700
```

From the firmware repo:

```bash
make flash-ppk2
# → build/flash-ppk2-result.json + build/ppk2-current.jsonl
```

### Power cycle only

```bash
.venv/bin/python shared/pscb_ppk2_cli.py cycle \
  --voltage-mv 3700 --hold-seconds 30
```

### Current monitor

```bash
.venv/bin/python shared/pscb_ppk2_cli.py monitor \
  --seconds 60 --current-log logs/ppk2-current.jsonl
```

## Options agents care about

| Flag | Default | Meaning |
|---|---|---|
| `--ppk` | `auto` | PPK2 ACM port, or discover |
| `--uart` | `$DRAGINO_PORT` or `/dev/ttyUSB0` | FTDI console/ISP |
| `--voltage-mv` | `3700` | Source voltage |
| `--off-seconds` | `2` | DUT off time during a cycle |
| `--settle-seconds` | `8` | Post-boot listen window |
| `--hold-seconds` | `0` | Keep port open / DUT ON after success |
| `--power-off` | off | DUT OFF before exit |
| `--hex` | required for flash | App Intel HEX @ `0x08007800` |
| `--result` | auto under `logs/` | Full result JSON path |
| `--current-log` | auto under `logs/` | Current JSONL path |
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

**`--result` JSON** (schema sketch):

```json
{
  "ok": true,
  "command": "flash-run",
  "ppk_port": "/dev/ttyACM0",
  "uart": "/dev/ttyUSB0",
  "voltage_mv": 3700,
  "current_log": "logs/ppk2-current-….jsonl",
  "steps": [
    {"name": "arm", "ok": true, "detail": {"avg_uA": 10600.0}},
    {"name": "isp_sync", "ok": true, "detail": {"isp_rts": false}},
    {"name": "flash", "ok": true, "detail": {"bytes": 49400, "address": "0x08007800"}},
    {"name": "boot", "ok": true, "detail": {"at_response": "OK", "openfw_seen": true}}
  ],
  "error": null
}
```

**`--current-log` JSONL** (one object per sample window):

```json
{"ts": 1790180313.07, "iso": "…", "uA": 11307.256, "n": 1023, "min_uA": 9917.7, "max_uA": 12607.2, "phase": "flash_write"}
```

`phase` tracks what the CLI was doing (`armed`, `isp_entry`, `flash_write`, `boot`, `monitor`, …).

## Sequence details (`flash-run`)

1. Open PPK2, load modifiers, **source-meter** mode, set voltage, DUT ON, start measuring.
2. Hold UART `RTS=ISP`, DUT power-cycle, sync STM32 ROM bootloader (`0x7F` / ACK).
3. Flash app pages only (`≥ 0x08007800`) via `pscb_app_isp.flash_image` (bootloader preserved).
4. Hold `RTS=Flash`, DUT power-cycle, listen for Dragino bootloader / `openfw` / `AT`→`OK`.
5. Write result JSON + current JSONL; exit. Use `--hold-seconds` if power must stay up.

Wired ISP popups / SW1 are **not** used. Soft `AT+ISP` is a separate, unfinished path.

## Troubleshooting

| Symptom | Check |
|---|---|
| PPK2 LED stays **green** | CLI must keep the port open; confirm `use_source_meter` + DUT ON in logs |
| Flat / bogus current | Port held open? Modifiers loaded? (warning printed if not) |
| ISP sync fails | RTS wiring; try `--isp-rts true` or leave `--auto-rts`; common GND |
| Flash refuses hex | Image must start at exactly `0x08007800` |
| `AT` empty after flash | Wait longer (`--settle-seconds`); confirm Flash-mode RTS after write |
| ACM port changed (`ttyACM0`→`1`) | Use `--ppk auto` (default); avoid PPK2 `RESET` command |

## Related

- Thin power-only helper (closes port on exit): [`ppk2_power_cycle.py`](ppk2_power_cycle.py)
- App-only ISP flash helpers: `PS-CB-NA/scripts/pscb_app_isp.py`
- Stock bootloader+app recover (prompts): `PS-CB-NA/scripts/recover_pscb.py`
- Firmware `make flash` (prompts) vs `make flash-ppk2` (this CLI)
