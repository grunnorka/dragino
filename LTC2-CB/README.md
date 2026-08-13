# LTC2-CB (temperature)

NB-IoT / LTE-M dual PT100 transmitter. Prefer **BLE** for config (UART sleeps quickly).

## PIN

Local label: `sensorInfo.txt` (or `DRAGINO_PIN_LTC2` / `LTC2_PIN` in env).  
Do **not** assume `DRAGINO_PIN` in `.env` is this unit (that is usually PS-CB-NA).

## Unlock / modem checklist

| Symptom | Action |
|---|---|
| `DRAGINO NB bootloader` loop | **SW1=Flash**, JP2 on, power-cycle; ACT **1–3s** only |
| `NBIOT did not respond` | Reseat SIM, antenna, APN (e.g. `lpwa.vodafone.is`); wait for `NBIOT has responded` |
| No `Password Correct` | Do not PIN-spam into bootloader — use `quiet` / `burst` scripts or BLE |

Shared unlock: `shared/dragino_uart.py` · fused watch: `shared/session_monitor.py --device ltc2`.

## Folder

| Path | Contents |
|---|---|
| `firmware/` | `LTC2-CB_v1.1.0.bin` / `.hex` (app @ `0x08007800`) |
| `manuals/` | Full MD + DOCX |
| `scripts/` | BLE/UART Railway config, dump, restore, flash helper |
| `PREPARE_CONNECT_CONFIG.md` | ThingsBoard prep handoff |
| `CONFIG_APPLIED.md` | Flash attempt notes |

## Common commands

From repo root:

```powershell
# Fused serial + MQTT (quiet unlock; exit 2 = modem/bootloader blocked)
python shared/session_monitor.py --device ltc2 --policy quiet --cycles 2

python LTC2-CB/scripts/ble_ltc2_config_railway.py
python LTC2-CB/scripts/ble_ltc2_verify.py
python LTC2-CB/scripts/quiet_pro35_railway_persist_test.py
python LTC2-CB/scripts/burst_uart_pro35_persist.py
python LTC2-CB/scripts/dump_ltc2_cfg.py
```

Flash only with SW1=ISP and correct wiring — see `CONFIG_APPLIED.md` and `scripts/flash_and_config_ltc2.py`.
