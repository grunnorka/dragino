# Dragino PS-CB-NA setup manual for LLM operators

**Audience:** another LLM or human operator configuring Dragino PS-CB-NA sensors from this workspace on a Fedora KDE host.

**Scope:** USB-TTL wiring, serial-port discovery, desktop popups, firmware updates, Vodafone/Síminn SIM config, and pointing the device at `vakt.systemat.is` or Railway MQTT.

**Last updated:** 2026-08-18.

---

## 1. What is in this workspace

```text
dragino/
├── .env                       # PIN + port (gitignored, secrets)
├── .venv/                     # Python environment
├── shared/
│   ├── prompt_user.py         # KDE desktop popups + notifications
│   ├── monitor.py             # Full serial logger + unlock + TDC setter
│   ├── dragino_uart.py        # Serial helpers (unlock, read, write)
│   └── at_session.py          # Async AT session parser
├── PS-CB-NA/
│   ├── firmware/
│   │   ├── PS-CB-NA_v1.2.1.hex              # app image @ 0x08007800
│   │   └── DRAGINO_NB_bootloader_v1.3.bin   # bootloader @ 0x08000000
│   ├── scripts/
│   │   ├── recover_pscb.py              # bootloader + app flash (ISP)
│   │   ├── configure_vodafone_vakt.py   # Vodafone + vakt.systemat.is
│   │   ├── configure_pscb_siminn_tb.py  # Síminn + vakt.systemat.is
│   │   ├── set_slow_intervals_pscb.py   # TDC/CLOCKLOG setter
│   │   └── fix_apn_null_vodafone_gdsp.py
│   ├── FIRMWARE_UPDATE.md
│   ├── SETUP.md
│   └── HANDOFF.md
└── docs/
    ├── RAILWAY_MQTT.md
    ├── VODAFONE_CONNECTIVITY.md
    ├── HIVEMQ_AND_BROKER_PERSISTENCE.md
    ├── SERIAL_UPLOAD_DIAGNOSTICS.md
    └── this file
```

Two sensor types exist in the repo but this manual focuses on **PS-CB-NA** (analog mA/V → `idc_input` / `vdc_input`).

---

## 2. Before touching hardware

### 2.1 Environment

```bash
cd /home/arnoreids/Documents/Cursor/dragino
. .venv/bin/activate           # or .venv/bin/python <script>
```

Required packages are already in `requirements.txt` (`pyserial`, `paho-mqtt`, `intelhex`, `stm32loader`).

### 2.2 `.env` format

Create or update `.env` from the device label:

```text
DRAGINO_PIN=your_6_digit_pin
DRAGINO_PORT=/dev/ttyUSB2
DRAGINO_BAUD=9600
```

- `DRAGINO_PIN` is the **6-digit password on the gift-box sticker** of the physical unit. It is **not** a SIM PIN.
- `DRAGINO_PORT` is the USB-TTL adapter the device is currently plugged into. It changes when cables are swapped.
- Never commit `.env`.

### 2.3 Serial port permissions

Ports are owned by `root:dialout` with mode `660`. The LLM **cannot sudo without a password**; ask the user to run:

```bash
sudo chmod 666 /dev/ttyUSB2
```

Use a desktop popup for this request (see §3).

---

## 3. Desktop popups and user interaction

Always use `shared/prompt_user.py` for any action that requires the human to press a button, flip a switch, or run a privileged command. It shows:

- a blocking KDE `zenity` dialog,
- a `notify-send` critical notification,
- a terminal banner.

### 3.1 Blocking action prompts

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
import prompt_user

prompt_user.step(
    "Press RESET on the board",
    [
        "1. The SW1 jumper must be in the Flash position.",
        "2. Press and release the RESET button.",
        "3. The LED should blink and boot text should appear.",
    ],
    ok_label="RESET pressed - I see boot text",
)
```

If the user clicks **Cancel** or the dialog is dismissed, the script exits with code 1.

### 3.2 Non-blocking info popups

Use for status updates that do not need confirmation:

```python
prompt_user.info(
    "Serial port permission needed",
    [
        "Please run this in a terminal:",
        "",
        "sudo chmod 666 /dev/ttyUSB2",
    ],
)
```

### 3.3 When to use popups

Use a popup every time the human must:
- run `sudo chmod 666`,
- flip SW1 between Flash and ISP,
- press RESET,
- provide a PIN/token that you cannot read from `.env`,
- confirm a risky step (factory reset, mass erase, etc.).

Do **not** use popups for routine status checks or for reading logs.

---

## 4. Identify the correct serial port

### 4.1 List USB adapters

```bash
ls -la /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
ls -la /dev/serial/by-id/
lsusb | grep -iE "ft232|pl2303|ftdi"
```

`/dev/serial/by-id/` is the most reliable way to map an adapter to a `/dev/tty*` node.

### 4.2 pyserial list

```bash
.venv/bin/python shared/monitor.py --list-ports
```

### 4.3 Which port is the Dragino?

The PS-CB-NA uses a separate USB-TTL adapter. The device itself does **not** enumerate as a USB device. Look for:

- FT232R USB UART (FTDI)
- USB-Serial Controller D (Prolific PL2303)

Ignore payment terminals (`PAX IM30`), modems, or other USB serial devices unless they are explicitly the target.

### 4.4 Sandbox warning

Cursor's sandbox hides `/dev/ttyUSB*`. Every command that opens a serial port or shows a popup must run with **full host permissions** (`required_permissions: ["all"]` in the Shell tool). If you forget, `ls /dev/ttyUSB*` will return empty even though the adapter is physically present.

---

## 5. Firmware update (ISP mode)

### 5.1 When to flash

Flash when:
- the user explicitly asks for a firmware update,
- the board is dark / LED does not light in Flash mode,
- the bootloader or app is erased,
- the device has an old firmware version that must be upgraded.

The workspace image is **official Dragino v1.2.1** (`PS-CB-NA_v1.2.1.hex`) plus bootloader **v1.3**. Always write both images to the correct addresses.

| Image | File | Address | Must not be relocated |
|---|---|---|---|
| Bootloader | `DRAGINO_NB_bootloader_v1.3.bin` | `0x08000000` | Yes |
| App | `PS-CB-NA_v1.2.1.hex` | `0x08007800` (carried in `.hex`) | Yes |

Writing the app `.hex` to `0x08000000` will erase the bootloader and brick the board.

### 5.2 Hardware setup for flashing

1. Wire USB-TTL: **GND↔GND, TX↔RX, RX↔TX** (3 wires only).
2. Move **SW1 to the ISP position**.
3. Press **RESET**.
4. No LED will light in ISP mode — this is expected.

### 5.3 Run the recovery script

```bash
.venv/bin/python PS-CB-NA/scripts/recover_pscb.py --port /dev/ttyUSB2
```

The script will show desktop popups for each manual step. It:
- prompts for ISP mode + RESET,
- synchronizes with the STM32 ROM bootloader at 115200 8E1,
- erases and writes the bootloader,
- erases and writes the app,
- verifies by read-back,
- prompts to move SW1 back to **Flash** + RESET,
- listens for the boot banner at 9600 and reports `BOOT_OK`.

A full flash takes ~2–3 minutes. Do **not** truncate the output.

### 5.4 After flashing

Fresh firmware resets the runtime config to factory defaults:
- `PRO=2,0` (UDP)
- `TDC=7200`
- `MQOS=0`
- `SERVADDR=NULL`, etc.

You must re-apply the full MQTT/ThingsBoard configuration after the flash.

---

## 6. Device configuration

### 6.1 Unlock

The device sleeps between uplinks. To wake it, either:
- wait for the next TDC cycle,
- press the **ACT** button for 1–3 seconds.

Use the idle-unlock pattern from `PS-CB-NA/scripts/set_slow_intervals_pscb.py` or `configure_vodafone_vakt.py`. It:
1. waits for the serial line to go quiet after an upload,
2. sends the PIN repeatedly until `Password Correct`,
3. sends AT commands and reboots with `ATZ`.

### 6.2 PIN

The PIN is `DRAGINO_PIN` from `.env`. Never hard-code it in a script or log. Redact it as `***PIN***` in any output.

### 6.3 Recommended payload profile for custom brokers

For `vakt.systemat.is` (private ThingsBoard) on PS-CB-NA, use **PRO=3,5** (JSON MQTT), not PRO=3,3.

`PRO=3,3` is the ThingsBoard payload type, but on this firmware it **reverts `SERVADDR` to `broker.hivemq.com` on reboot**, which is the well-known "HiveMQ jump" bug. `PRO=3,5` keeps the custom `SERVADDR` and token across reboots.

```text
AT+PRO=3,5
AT+SERVADDR=167.235.104.181,1883
AT+UNAME=<device_token>
AT+PWD=NULL
AT+PUBTOPIC=v1/devices/me/telemetry
AT+SUBTOPIC=v1/devices/me/attributes
AT+CLIENT=null
AT+MQOS=1
AT+TLSMOD=0,0
AT+BKDNS=1,0,167.235.104.181,1883
AT+APN=<correct_apn>
AT+TDC=<interval>
```

### 6.4 APN by SIM type

| SIM | IMSI prefix | APN command | Notes |
|---|---|---|---|
| **Vodafone GDSP** (global IoT roaming) | `90128` | `AT+APN=NULL` | Network supplies APN; do not use `lpwa.vodafone.is` |
| **Vodafone Iceland / Sýn** | `27402` | `AT+APN=lpwa.vodafone.is` | Local operator APN |
| **Síminn** | `27401` | `AT+APN=internet` | Standard consumer/IoT APN |

Read `AT+IMSI=?` after unlocking to decide. If the IMSI starts with `90128`, use `NULL`. If in doubt, prefer `NULL` for Vodafone-branded SIMs and let the network reject it; a wrong explicit APN (`lpwa.vodafone.is`) on a GDSP SIM will fail PDP activation.

### 6.5 Order matters

1. Unlock (`<PIN>` or `AT+PIN=<PIN>`)
2. `AT+PRO=3,5`
3. `AT+SERVADDR=...`
4. Auth/topics/CLIENT
5. `AT+MQOS=1`, `AT+TLSMOD=0,0`
6. `AT+BKDNS=1,0,...`
7. `AT+APN=...`
8. `AT+TDC=...`
9. `AT+CLOCKLOG=...` (optional)
10. Verify with `AT+CFG`
11. `ATZ` to persist
12. After reboot, re-read `AT+SERVADDR=?`, `AT+PRO=?`, `AT+BKDNS=?` to confirm no HiveMQ.

### 6.6 TDC and CLOCKLOG limits on firmware v1.2.1

Live testing on `PS-CB-NA_v1.2.1` shows:

- `AT+TDC` values above **180 s do not stick** once the device has been set to `180`. `AT+TDC=1800` and `AT+TDC=86400` return `OK` but `AT+TDC=?` still reads `180`. This appears to be a firmware quirk: the value is only changeable from factory default, not from the `180` test interval.
- `AT+CLOCKLOG` interval is stored in a single byte: values above **255 wrap** (e.g., `360` becomes `104`). Use `240` minutes (4 h) as the practical maximum.

**Workaround for 2-hour uploads:**
1. Send `AT+FDR1` (partial reset). It reboots immediately and does **not** return `OK`.
2. After reboot, `TDC` will be back to the factory default of **7200 s** (2 h).
3. Re-apply `AT+PRO=3,5`, `AT+MQOS=1`, and `AT+BKDNS=1,0,...` (these were reset by `FDR1`).
4. **Do not send `AT+TDC=180`** — leave it at `7200`.
5. `SERVADDR`, `UNAME`, `PWD`, `PUBTOPIC`, `SUBTOPIC`, `APN`, and `PWORD` survive `AT+FDR1`, so they do not need to be re-entered.

If you do not want the risk of a reset, leave `TDC=180` (3-minute uploads) and rely on the slower sample interval from `AT+CLOCKLOG`.

### 6.7 Persistence check

After `ATZ`, always confirm the custom broker survived. Fail criteria:
- `SERVADDR` contains `broker.hivemq.com`
- `BKDNS` contains a public HiveMQ IP (`3.127.x.x`, `18.198.x.x`, etc.)
- `PRO` is not `3,5`

If any of these happen, re-apply the full sequence (do not just fix one field).

---

## 7. Target brokers

### 7.1 vakt.systemat.is (ThingsBoard)

| Setting | Value |
|---|---|
| `SERVADDR` | `167.235.104.181,1883` or `vakt.systemat.is,1883` |
| `UNAME` | ThingsBoard device access token |
| `PWD` | `NULL` |
| `PUBTOPIC` | `v1/devices/me/telemetry` |
| `SUBTOPIC` | `v1/devices/me/attributes` |
| `CLIENT` | `null` |
| `PRO` | `3,5` |
| `TLSMOD` | `0,0` |

Use the IP form if DNS is unreliable.

### 7.2 Railway MQTT

See `docs/RAILWAY_MQTT.md` and `PS-CB-NA/scripts/configure_pscb_railway.py`. The broker is `altaria.proxy.rlwy.net:33239` (or `66.33.22.220:33239`).

---

## 8. Verification checklist

After configuration:

- [ ] Device unlocked (`Password Correct`)
- [ ] `AT+PRO=?` returns `3,5`
- [ ] `AT+SERVADDR=?` returns `167.235.104.181,1883` (or Railway proxy)
- [ ] `AT+BKDNS=?` matches the same host/IP, no HiveMQ
- [ ] `AT+APN=?` matches the SIM type
- [ ] `AT+TDC=?` and `AT+CLOCKLOG=?` are the intended values
- [ ] `ATZ` reboot completes
- [ ] Post-reboot query still shows custom `SERVADDR` and `PRO=3,5`
- [ ] One successful upload appears on the target server

---

## 9. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `ls /dev/ttyUSB*` empty | Sandbox restriction | Run with `required_permissions: ["all"]` |
| `Permission denied` on port | Mode `660` | Ask user to `sudo chmod 666 /dev/ttyUSBx` |
| `Password Incorrect` | Wrong `DRAGINO_PIN` | Read the sticker on the physical unit |
| `Password timeout` | Device sleeping | Wait for TDC cycle or press ACT 1–3 s |
| `NBIOT did not respond` | No SIM, no antenna, wrong APN, or SW1=ISP | Check SIM/antenna, set SW1=Flash, wait for `NBIOT has responded` |
| `Failed to send` after `Upload data successfully` | Harmless TCP teardown | Ignore; data already reached the server |
| `Failed to send` + no upload | Radio not attached | Check APN, signal strength, wait or `ATZ` |
| `SERVADDR=broker.hivemq.com` after reboot | `PRO=3,3` used | Switch to `PRO=3,5` and re-apply everything |
| `TDC` does not change | Firmware quirk on v1.2.1 | Document as limitation; try `ATZ` after setting, or test during a fully idle window |

---

## 10. Useful commands

```bash
# List ports
.venv/bin/python shared/monitor.py --list-ports

# Flash/recover
.venv/bin/python PS-CB-NA/scripts/recover_pscb.py --port /dev/ttyUSB2

# Vodafone + vakt.systemat.is (token in env)
export TB_TOKEN=<device_token>
.venv/bin/python PS-CB-NA/scripts/configure_vodafone_vakt.py --port /dev/ttyUSB2

# Síminn + vakt.systemat.is (token in env)
export TB_TOKEN=<device_token>
.venv/bin/python PS-CB-NA/scripts/configure_pscb_siminn_tb.py --port /dev/ttyUSBx

# Set slowest supported intervals (TDC=1800, CLOCKLOG=240,6)
.venv/bin/python PS-CB-NA/scripts/set_slow_intervals_pscb.py --port /dev/ttyUSB2

# Fix APN to NULL for Vodafone GDSP
.venv/bin/python PS-CB-NA/scripts/fix_apn_null_vodafone_gdsp.py --port /dev/ttyUSB2
```

---

## 11. Safety rules

1. **Never mass-erase** flash unless deliberately recovering a bricked bootloader.
2. A `.hex` carries its own address; a `.bin` does not. Never relocate a `.bin` app.
3. Only one process may hold the serial port at a time.
4. Do not paste PINs, tokens, or passwords into committed files or logs.
5. Do not use `AT+PRO=3,3` for custom brokers on PS-CB-NA.
6. After `ATZ`, always re-check `SERVADDR` and `BKDNS` for HiveMQ.
