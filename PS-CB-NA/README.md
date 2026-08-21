# PS-CB-NA (analog sensor)

NB-IoT / LTE-M analog node (`idc_input` / `vdc_input`).

**Start here if you are an LLM or new operator:** [HANDOFF.md](HANDOFF.md)  
Then: [SETUP.md](SETUP.md) (Railway MQTT) · [FIRMWARE_UPDATE.md](FIRMWARE_UPDATE.md) (flash / dark board).

## PIN

Use workspace `.env` key `DRAGINO_PIN` (gift-box sticker). Do not commit the PIN.

## Folder

| Path | Contents |
|---|---|
| `HANDOFF.md` | Current status + next steps for the next agent |
| `SETUP.md` | Railway MQTT configure playbook |
| `FIRMWARE_UPDATE.md` | Bootloader recovery, flash map, AT pitfalls |
| `firmware/` | Shipped images (`PS-CB-NA_v1.2.1.hex`, bootloader); `reference/` is gitignored upstream NBSN95 source |
| `manuals/` | Product manual extract |
| `scripts/` | Flash, verify, reset/reapply, MQTT fix, port diag |

## Common commands (Linux)

From repo root:

```bash
.venv/bin/python PS-CB-NA/scripts/verify_pscb.py
.venv/bin/python PS-CB-NA/scripts/fix_pscb_mqtt.py
.venv/bin/python PS-CB-NA/scripts/reset_and_apply_pscb.py
.venv/bin/python shared/session_monitor.py --device ps-cb --policy stable --cycles 3
```

Flashing (SW1=ISP): `.venv/bin/python PS-CB-NA/scripts/recover_pscb.py`

Broker docs: [../docs/RAILWAY_MQTT.md](../docs/RAILWAY_MQTT.md).
