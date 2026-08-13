# PS-CB-NA (analog sensor)

NB-IoT / LTE-M analog node (`idc_input` / `vdc_input`). UART console default **COM8 @ 9600**.

**LLM / operator setup playbook:** [SETUP.md](SETUP.md) (Railway MQTT, AT order, ATZ check, uplink monitoring).

## PIN

Use workspace `.env` key `DRAGINO_PIN` (gift-box sticker). Do not commit the PIN.

## Folder

| Path | Contents |
|---|---|
| `SETUP.md` | Step-by-step Railway MQTT configure playbook |
| `firmware/` | `PS-CB_v1.2.0.hex` |
| `manuals/` | Full MD export, PDF stub, structured extract |
| `scripts/` | Railway configure, GPS helpers, TB diag, uplink observe |

## Common commands

From repo root:

```powershell
python shared/monitor.py --port COM8 --unlock-now --debug --poll 60 --set-cycle 120
python PS-CB-NA/scripts/configure_pscb_railway.py
python PS-CB-NA/scripts/configure_pscb_tb_pro35.py
python PS-CB-NA/scripts/observe_uplink_cycles.py
python PS-CB-NA/scripts/tb_diag_once.py
```

AT handoff (ThingsBoard / older notes): [../docs/AT_COMMANDS_HANDOFF.md](../docs/AT_COMMANDS_HANDOFF.md).
