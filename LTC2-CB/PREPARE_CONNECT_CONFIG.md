# LTC2-CB — Prepare to Connect & Configure (ThingsBoard)

**Status:** Prep / handoff only — **do not** flash firmware, **do not** factory-reset (`AT+FDR` / `AT+FDR1`).  
**Date:** 2026-08-06  
**Device:** Dragino **LTC2-CB** (NB-IoT/LTE-M **temperature** transmitter, 2× PT100)  
**Target broker:** same as PPS-CB-NA — `vakt.systemat.is` / `167.235.104.181:1883`  
**Token:** `PLACEHOLDER` (no LTC2 device token in workspace `.env`)

---

## 0. Documents in this folder

| File | Role |
|---|---|
| `manuals/LTC2-CB.md` | Primary manual export (full text + embedded images) — **source of truth** |
| `manuals/LTC2-CB.docx` | Same manual as Word |
| `firmware/LTC2-CB_v1.1.0.bin` | Firmware image — **do not flash** in this prep session |
| `sensorInfo.txt` | Local label notes: type + AT PIN |
| `PREPARE_CONNECT_CONFIG.md` | This handoff |

Official wiki mirror: [LTC1/LTC2-CB](https://wiki.dragino.com/docs/NB-IoT/temperature-humidity-sensors/ltc1-ltc2-cb/) · CB general MQTT/ThingsBoard: [General Configure for -CB & -CS](https://wiki.dragino.com/docs/NB-IoT/general-configuration-manual/general-configure-for-cb-cs-models-nb-iot-lte-m/)

Cross-check lessons (PPS-CB-NA / shared stack):

- `../docs/LLM_SENSOR_SETUP_MANUAL.md`
- `../docs/AT_COMMANDS_HANDOFF.md`
- `../docs/HIVEMQ_AND_BROKER_PERSISTENCE.md`
- `../docs/TELEMETRY_8_SLOTS.md`

---

## 1. Product identity

| Item | LTC2-CB |
|---|---|
| What it measures | **Temperature** (°C) via **PT100** probes |
| Channels | **2** ADC channels → `channel1_temp`, `channel2_temp` |
| Radio | **NB-IoT / LTE-M** (module **BC660K-GL**), not LoRaWAN |
| Uplink protocols | MQTT / MQTTs / TCP / UDP / CoAP |
| Power | 8500 mAh Li-SOCl₂ (CB) or solar+Li-ion (CS) |
| Config | BLE (recommended) or UART TTL |
| Default TDC | **7200 s** (2 hours) until changed |

JSON example keys (Type=5): `channel1_temp`, `channel2_temp`, `temp_alarm`, `battery`, `signal`, `time`, plus CLOCKLOG slots `"1"`…`"d"` as `[ch1, ch2, timestamp]`.  
Disconnected PT100 decode: **-327.6** °C means probe not connected (HEX path).

**Not** the same as PPS-CB-NA (analog mA/V level probe → `idc_input` / `vdc_input`).

---

## 2. Credentials / `.env`

| Source | Key | Value / note |
|---|---|---|
| `LTC2-CB/sensorInfo.txt` | AT PIN | **`358613`** (box-style 6-digit; OK to use for Dragino work) |
| Workspace `.env` | `DRAGINO_PIN` | `357319` — **PPS-CB-NA only**, not this unit |
| Workspace `.env` | `DRAGINO_PORT` / `DRAGINO_BAUD` | `COM8` / `9600` — PPS FTDI defaults |
| Workspace `.env` | LTC2 token | **None** — use `PLACEHOLDER` until ThingsBoard device is created |

**Suggested later (not applied now):** add e.g. `LTC2_PIN=358613` and `LTC2_TOKEN=…` so PPS and LTC2 do not share one PIN/token.

---

## 3. Hardware / serial checklist (before plugging)

### Ports observed (2026-08-06, LTC2 **not** connected)

| Port | What it is |
|---|---|
| **COM8** | **PPS-CB-NA** USB Serial (FTDI `VID_0403+PID_6001`) — **occupied / reserved** |
| COM3 | Intel AMT SOL — ignore |
| COM9 / COM10 | **PPK2** (per prior workspace notes) — **not present** right now |

**When LTC2 is plugged via its own USB-TTL:** expect a **new** COM (or the next free FTDI COM). Do **not** open COM8 while PPS is still attached. Confirm with:

```powershell
python shared/monitor.py --list-ports
# or
Get-PnpDevice -Class Ports -Status OK
```

### UART wiring (same CB family as PPS)

- Baud: **9600 8N1** (workspace CB practice; trailing newline on every AT line)
- SW1 = **Flash** (not ISP — ISP = no console / upgrade-only)
- JP2 jumper **installed** = powered on
- USB-TTL: GND↔GND, TX↔RX, RX↔TX
- Prefer `python shared/session_monitor.py --device ltc2 --policy quiet` (or `burst`) after wake; exit **2** means modem/bootloader blocked (SIM/antenna/SW1) — not a PRO bug
- UART persist helpers use shared unlock: `scripts/quiet_pro35_railway_persist_test.py`, `scripts/burst_uart_pro35_persist.py`
- Optional: press ACT **1–3 s** to force uplink + wake BLE; **>3 s** = activate from deep sleep / OTA window

### Power / PPK2

- Battery-powered CB: keep awake only as long as needed; modem poll wakes radio.
- If measuring with PPK2 later: use COM9/COM10 when PPK2 is attached; do not confuse with sensor UART.
- Firmware note in manual: UART firmware upgrade requires disconnecting sensor/baseboard GND conflict first — **N/A for this prep** (no flash).

### Pre-config hardware checklist

- [ ] SIM inserted (GE version needs APN; confirm operator)
- [ ] JP2 on, SW1 = Flash
- [ ] PT100(s) connected (expect real °C, not −327.6)
- [ ] Free COM identified (not COM8 if PPS still plugged)
- [ ] Serial tool / `shared/monitor.py` ready at **9600**
- [ ] ThingsBoard device created → access token ready to paste
- [ ] Do **not** run `AT+FDR` / `AT+FDR1`
- [ ] Do **not** flash `firmware/LTC2-CB_v1.1.0.bin`

---

## 4. Shared stack pitfalls (same as PPS-CB-NA)

These apply to LTC2-CB — same Dragino **CB** MQTT / DNS stack:

| Pitfall | Rule |
|---|---|
| `AT+PRO=3,3` | ThingsBoard payload — **“will also configure other default server to ThingsBoard.”** Always re-set `SERVADDR` **after** PRO. |
| HiveMQ demo host | Factory / docs example: `broker.hivemq.com,1883` |
| `AT+BKDNS` | Sticky resolved IP; after HiveMQ once, DNS fail can still dial HiveMQ. Clear with `AT+BKDNS=1,0` (or pin private IP). |
| `AT+FDR` / `AT+FDR1` | Full wipe → demo defaults again. **Do not use.** |
| `SUBTOPIC=#` | Avoid on shared/public brokers; use `v1/devices/me/attributes` |
| Order | Unlock → PRO → **SERVADDR** → auth/topics → BKDNS → TDC/CLOCKLOG → CFG verify |

---

## 5. Exact AT sequence (ThingsBoard → vakt / 167.235…)

Prefer **IP** if DNS to hostname is flaky (PPS lessons settled on `167.235.104.181,1883` with matching BKDNS). Hostname alternative: `vakt.systemat.is,1883`.

**Unlock first** (every line ends with newline):

```text
358613
```

or:

```text
AT+PIN=358613
```

**Set ThingsBoard MQTT (token style) — order matters:**

```text
AT+PRO=3,3
AT+SERVADDR=167.235.104.181,1883
AT+UNAME=PLACEHOLDER
AT+PWD=NULL
AT+PUBTOPIC=v1/devices/me/telemetry
AT+SUBTOPIC=v1/devices/me/attributes
AT+CLIENT=null
AT+MQOS=1
AT+TLSMOD=0,0
AT+BKDNS=1,0,167.235.104.181,1883
AT+CLOCKLOG=1,65535,5,8
AT+TDC=1800
AT+CFG
```

Hostname variant (if preferred over IP):

```text
AT+SERVADDR=vakt.systemat.is,1883
AT+BKDNS=1,0
```

…then after one good resolve, confirm `AT+BKDNS=?` shows **167.235.104.181** (or your TB IP), **not** a HiveMQ public IP (`3.127…`, `18.198…`, etc.).

**Replace `PLACEHOLDER`** with the ThingsBoard **device access token** for this LTC2 before applying.

Optional (only if SIM needs it — match PPS if same carrier):

```text
AT+APN=?
AT+APN=lpwa.vodafone.is
```

(Use the correct APN for the SIM in *this* LTC2; do not assume without checking.)

---

## 6. Interval recommendations

Mirror PPS research target (**sample 5 min / upload 30 min**):

| Goal | Command | Notes |
|---|---|---|
| Sample every **5 min** | `AT+CLOCKLOG=1,65535,5,8` | `c` = minutes; `d=8` history slots (~40 min coverage) |
| Upload every **30 min** | `AT+TDC=1800` | TDC in **seconds** |
| Factory default upload | `AT+TDC=7200` | 2 hours — change deliberately |

CLOCKLOG slots on LTC2 are `[channel1_temp, channel2_temp, sampling_time]` (not mA/V).  
Sync network time before CLOCKLOG; otherwise it may apply only after reset (`ATZ` if needed — still prefer no FDR).

LTC2-native alarm extras (optional, not required for MQTT uplink):

| Command | Role |
|---|---|
| `AT+WMOD=0/1` | Alarm mode off/on |
| `AT+CITEMP=<min>` | Alarm check interval (default 5 min) |
| `AT+ARTEMP=lo1,hi1,lo2,hi2` | °C thresholds per channel |

---

## 7. Verify CFG checklist

After unlock:

```text
AT+CFG
AT+MODEL=?
AT+SERVADDR=?
AT+BKDNS=?
AT+PRO=?
AT+UNAME=?
AT+PWD=?
AT+PUBTOPIC=?
AT+SUBTOPIC=?
AT+MQOS=?
AT+TLSMOD=?
AT+TDC=?
AT+CLOCKLOG=?
AT+APN=?
AT+GETSENSORVALUE
AT+LDATA
```

**Pass criteria**

| Check | Pass if |
|---|---|
| Broker | `SERVADDR` = `167.235.104.181,1883` or `vakt.systemat.is,1883` — **not** `broker.hivemq.com` |
| Sticky DNS | `BKDNS` has **no** HiveMQ IP |
| Profile | `PRO=3,3` |
| Auth | `UNAME` = real TB token (not PLACEHOLDER); `PWD=NULL` |
| Topics | `v1/devices/me/telemetry` + `v1/devices/me/attributes` (not `#`) |
| Upload | `TDC=1800` |
| Sample | `CLOCKLOG=1,65535,5,8` |
| Sense | Live temps look sane; not −327.6 on connected channels |
| Radio | Valid APN; attach before expecting MQTT |

Then in ThingsBoard: see `channel1_temp` / `channel2_temp` (and CLOCKLOG keys if enabled). Serial: button uplink 1–3 s; look for successful upload **without** HiveMQ + `Failed to send` pattern.

---

## 8. Differences vs PPS-CB-NA

| Topic | PPS-CB-NA | LTC2-CB |
|---|---|---|
| Measurand | Analog IDC/VDC (mA / V); mm via TB math | **PT100 temperature** (°C), 2 channels |
| Telemetry keys | `idc_input`, `vdc_input`, … | `channel1_temp`, `channel2_temp`, `temp_alarm`, … |
| Probe config | `AT+PROBE` (span hint for server) | Probe hardware variants (DR-SI/LT/HT/…); alarm via `WMOD`/`ARTEMP` |
| PIN (this workspace) | `.env` `DRAGINO_PIN=357319` | `sensorInfo.txt` **`358613`** |
| Token in `.env` | Used on PPS unit historically | **None yet** → PLACEHOLDER |
| COM today | **COM8** FTDI | **Unknown until plugged** — must not steal COM8 while PPS attached |
| MQTT / TB / HiveMQ / BKDNS / TDC / CLOCKLOG | Shared CB stack | **Same rules and AT shapes** |
| Firmware file in folder | (elsewhere) | `firmware/LTC2-CB_v1.1.0.bin` present — leave unused |

---

## 9. First session plan (when ready to connect)

1. Leave PPS on COM8 alone, or disconnect PPS if sharing one FTDI cable.
2. Plug LTC2 UART → note new COM → `9600 8N1`.
3. Unlock with **`358613`** → `AT+CFG` (baseline dump to `logs/`).
4. Create TB device + token; replace PLACEHOLDER.
5. Run §5 sequence (PRO → SERVADDR → auth → BKDNS → intervals).
6. Run §7 verify; confirm no HiveMQ.
7. Optional: one button uplink; watch TB + serial.

**Stop conditions:** unexpected factory wipe request, ISP mode, firmware flash prompts, or COM conflict with PPS — abort and reassess.

---

## Bottom line

LTC2-CB is a **dual-channel PT100 NB-IoT temperature** node on the **same MQTT/ThingsBoard AT stack** as PPS-CB-NA. Prep PIN is **`358613`**; broker target matches PPS (`167.235.104.181:1883` / `vakt.systemat.is`); token still **PLACEHOLDER**. Connect later on a **free COM** (not COM8 while PPS is up), apply §5 in order, and verify SERVADDR/BKDNS are not HiveMQ.
