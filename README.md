# Dragino sensor workspace

Two NB-IoT / LTE-M sensor nodes plus shared UART/MQTT tooling.

| Device | Folder | What it measures |
|---|---|---|
| **PS-CB-NA** | [PS-CB-NA/](PS-CB-NA/) | Analog mA/V (`idc_input` / `vdc_input`) — UART on COM8 |
| **LTC2-CB** | [LTC2-CB/](LTC2-CB/) | Dual PT100 temperature — BLE preferred, UART also |

## Layout

```text
shared/          UART monitor, Railway MQTT helpers, PPK2 power
mqtt/            Mosquitto + TCP-1883 Docker for Railway
dashboard/       Fleet UI + MQTT→Postgres ingest (Railway web/ingest)
docs/            Living AT / broker / telemetry notes
PS-CB-NA/        Analog node (firmware, manuals, scripts)
LTC2-CB/         Temp node (firmware, manuals, scripts)
archive/         One-off experiments, research, debug zips, scrapes
logs/            Session captures (gitignored)
```

## Setup

```powershell
cd C:\Users\Arnor\Downloads\dragino
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env` (gitignored) — **PS-CB-NA PIN** (gift-box sticker):

```powershell
# .env
DRAGINO_PIN=your_6_digit_pin
DRAGINO_PORT=COM8
DRAGINO_BAUD=9600
```

MQTT secrets: copy `railway-mqtt.env.example` → `railway-mqtt.local.env` and set `MQTT_PASS`. Details: [docs/RAILWAY_MQTT.md](docs/RAILWAY_MQTT.md).

Run scripts from the **repo root** so `.env` / `logs/` resolve correctly.

## Shared tools

```powershell
# Preferred: fused serial unlock + Railway MQTT (one process, summary JSON)
python shared/session_monitor.py --device ps-cb --policy stable --cycles 3
python shared/session_monitor.py --device ltc2 --policy quiet --cycles 2

# Full UART capture (unlocked only after Password Correct)
python shared/monitor.py --port COM8 --baud 9600 --unlock-now --debug

# Unlock + poll + set TDC=120 (PIN from .env)
python shared/monitor.py --port COM8 --unlock-now --debug --poll 60 --set-cycle 120

python shared/monitor.py --list-ports
python shared/mqtt_smoke_railway.py
python shared/mqtt_listen_railway.py   # broker-only (no COM)
python shared/railway_mqtt.py --at --device-id ps-cb --use-ip

# Fleet dashboard (DATABASE_URL + BASIC_AUTH_PASSWORD; devices keyed by IMEI — see dashboard/README.md)
PYTHONPATH=. python -m dashboard.ingest
PYTHONPATH=. uvicorn dashboard.web:app --port 8000
```

**Session exit codes:** `0` success · `2` unlock/modem blocked · `3` radio fail · `4` broker down · `5` usage/PIN.

Unlock lives in `shared/dragino_uart.py` (`quiet` / `burst` / `stable`). Cycle classing: `shared/uplink_classify.py`.

## Configure devices

```powershell
# PS-CB-NA → Railway MQTT
python PS-CB-NA/scripts/configure_pscb_railway.py
python PS-CB-NA/scripts/observe_uplink_cycles.py --port COM8

# LTC2-CB → Railway MQTT (BLE preferred; press ACT 1–3s when prompted)
python LTC2-CB/scripts/ble_ltc2_config_railway.py

# LTC2-CB UART PRO=3,5 + ATZ persist (quiet / burst)
python LTC2-CB/scripts/quiet_pro35_railway_persist_test.py
python LTC2-CB/scripts/burst_uart_pro35_persist.py

# LTC2-CB UART dump / restore helpers
python LTC2-CB/scripts/dump_ltc2_cfg.py
```

Device-specific notes: [PS-CB-NA/README.md](PS-CB-NA/README.md), [LTC2-CB/README.md](LTC2-CB/README.md).

## Docs

| Doc | Topic |
|---|---|
| [PS-CB-NA/SETUP.md](PS-CB-NA/SETUP.md) | PS-CB-NA Railway MQTT setup playbook (LLM/operator) |
| [docs/AT_COMMANDS_HANDOFF.md](docs/AT_COMMANDS_HANDOFF.md) | PS-CB-NA MQTT / ThingsBoard AT sequence |
| [docs/RAILWAY_MQTT.md](docs/RAILWAY_MQTT.md) | Self-hosted Mosquitto on Railway + dashboard services |
| [dashboard/README.md](dashboard/README.md) | Fleet UI + ingest env / deploy notes |
| [docs/HIVEMQ_BROKER_SWITCH.md](docs/HIVEMQ_BROKER_SWITCH.md) | Why SERVADDR jumps to HiveMQ |
| [docs/TELEMETRY_8_SLOTS.md](docs/TELEMETRY_8_SLOTS.md) | CLOCKLOG / 8-slot history |

## Hardware notes

- SW1 = **Flash** for normal use (ISP only when flashing)
- USB-TTL: GND↔GND, TX↔RX, RX↔TX
- Device sleeps between uplinks; console may be quiet until TX / ACT / `AT+DEBUG=1`
- After any `AT+PRO=…` (especially `3,3`), re-check `AT+SERVADDR` — see HiveMQ doc
