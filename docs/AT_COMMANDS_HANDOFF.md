# AT Commands Handoff — PS-CB-NA MQTT / ThingsBoard

**Audience:** serial agent (COM8)  
**Do not** open COM8 from the manual-parser agent.  
**Sources:** `../PS-CB-NA/manuals/PPS-CB-NA - NB-IoT_LTE-M Analog Sensor.md` (primary), structured extract `../PS-CB-NA/manuals/PPS-CB-NA_NB-IoT_LTE-M_Analog_Sensor.md`, stack notes for `AT+BKDNS` / HiveMQ example.  
**Context:** unit may show `AT+SERVADDR=broker.hivemq.com,1883` while still holding ThingsBoard token/topics — see `HIVEMQ_BROKER_SWITCH.md`.

---

## Unlock

```text
<6-digit PIN from DRAGINO_PIN / box label>
# or
AT+PIN=<6-digit>
```

Need trailing newline on every command.

---

## WARNING: `AT+PRO=3,3` rewrites server defaults

Manual (ThingsBoard Payload Type=3):

> it will also configure other default server to ThingsBoard.

**Rule:** After **any** `AT+PRO=…` (especially `3,3`), immediately:

1. `AT+SERVADDR=?`
2. Re-set private host if HiveMQ / wrong host appears
3. Clear/fix `AT+BKDNS`
4. Confirm with `AT+CFG`

Also avoid `AT+FDR` / `AT+FDR1` unless a full wipe is intended (restores factory/demo server settings; docs use `broker.hivemq.com,1883` as SERVADDR example).

---

## SET private MQTT / ThingsBoard (token style)

Replace placeholders. Order matters: if you set `PRO=3,3`, **re-apply SERVADDR after it**.

```text
AT+PRO=3,3
AT+SERVADDR=<YOUR_TB_HOST>,1883
AT+UNAME=<DEVICE_ACCESS_TOKEN>
AT+PWD=NULL
AT+PUBTOPIC=v1/devices/me/telemetry
AT+SUBTOPIC=v1/devices/me/attributes
AT+CLIENT=null
AT+MQOS=1
AT+TLSMOD=0,0
AT+BKDNS=1,0
AT+TDC=180
```

### Exact command shapes

| Goal | Command |
|---|---|
| Set MQTT host/port | `AT+SERVADDR=<host>,<port>` |
| Set username / token | `AT+UNAME=<token_or_user>` |
| Set password | `AT+PWD=<password>` or `AT+PWD=NULL` |
| Set publish topic | `AT+PUBTOPIC=<topic>` |
| Set subscribe topic | `AT+SUBTOPIC=<topic>` |
| Set QoS | `AT+MQOS=0` / `1` / `2` |
| Set client ID | `AT+CLIENT=<id>` or `null` |
| Clear sticky DNS cache | `AT+BKDNS=1,0` |
| Set uplink interval (s) | `AT+TDC=180` |
| TLS off (plain MQTT) | `AT+TLSMOD=0,0` |
| Dump all | `AT+CFG` |

TLS / MQTTs (if required): use port `8883` and set `AT+TLSMOD` per broker (do not leave HiveMQ host).

### Dragino ThingsBoard.Cloud integration-style (device name topics)

Only if matching TB MQTT **integration** (not access-token telemetry):

```text
AT+PRO=3,3
AT+SERVADDR=<TB_MQTT_HOST>,1883
AT+PUBTOPIC=<device name>
AT+SUBTOPIC=<device name>
AT+CLIENT=<device name>
AT+UNAME=<device name>
AT+PWD=<device name or token>
AT+BKDNS=1,0
```

This unit has been using **token + `v1/devices/me/telemetry`** — prefer that unless TB is configured for integration topics.

---

## Clear BKDNS (HiveMQ sticky IP)

```text
AT+BKDNS=1,0
AT+BKDNS=?
```

If `=?` still shows a public HiveMQ IP (`3.127…`, `18.198…`, etc.), set `c` to the private server IP after one good resolve, e.g.:

```text
AT+BKDNS=1,0,<PRIVATE_IP>,1883
```

Do not leave HiveMQ in `c`.

---

## VERIFY (must pass before declaring success)

```text
AT+CFG
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
AT+APN=?
```

**Pass criteria:**

- `SERVADDR` = private TB host (NOT `broker.hivemq.com`)
- `BKDNS` has no HiveMQ IP
- `PRO` = `3,3` (if ThingsBoard payload desired)
- Topics/auth match chosen TB mode
- `TDC` = `180` (3 min) if that is still the project target

---

## Copy-paste minimal fix (HiveMQ → private TB)

Assume unlock already done; token/topics already correct; only broker wrong:

```text
AT+SERVADDR=<YOUR_TB_HOST>,1883
AT+BKDNS=1,0
AT+CFG
```

If you must re-apply ThingsBoard mode:

```text
AT+PRO=3,3
AT+SERVADDR=<YOUR_TB_HOST>,1883
AT+UNAME=<DEVICE_ACCESS_TOKEN>
AT+PWD=NULL
AT+PUBTOPIC=v1/devices/me/telemetry
AT+SUBTOPIC=v1/devices/me/attributes
AT+BKDNS=1,0
AT+TDC=180
AT+CFG
```

---

## Do / Don't

| Do | Don't |
|---|---|
| Re-set SERVADDR after every `AT+PRO` | Assume `PRO=3,3` alone points at private TB |
| Clear BKDNS after broker change | Leave HiveMQ IP in BKDNS |
| Prefer narrow SUBTOPIC on shared brokers | Use `SUBTOPIC=#` on public HiveMQ |
| Use `AT+CFG` to confirm | Use `AT+FDR` / `AT+FDR1` casually |
