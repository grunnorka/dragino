# HiveMQ / HiveMQTT unexpected broker switch (PS-CB-NA)

Last updated: 2026-08-06  
Device: Dragino **PS-CB-NA** (firmware **v1.1.4**, stack **D-BG95-003**)  
Serial: **COM8 @ 9600 8N1**, unlock PIN from `.env` (`DRAGINO_PIN`)

## Symptom

After changing some settings, the sensor’s MQTT server is no longer the private ThingsBoard host. Instead `AT+CFG` / `AT+SERVADDR=?` shows the public HiveMQ broker:

```text
AT+SERVADDR=broker.hivemq.com,1883
```

Serial uplink logs then show successful TCP/MQTT connect to a HiveMQ-resolved IP (e.g. `3.127.172.15:1883` or `18.198.118.51:1883`), followed by ThingsBoard-style publish attempts and often **`Failed to send`**.

This feels like an “unexpected switch to HiveMQTT” because ThingsBoard credentials/topics may still look correct while the **host** is wrong.

## Current observed config (live COM8, 2026-08-06)

| Field | Value | Notes |
|---|---|---|
| `AT+PRO` | `3,3` | MQTT + **ThingsBoard payload type** |
| `AT+SERVADDR` | `broker.hivemq.com,1883` | **Public HiveMQ**, not private TB |
| Resolved IP | `3.127.172.15,1883` | Shown under SERVADDR in CFG |
| `AT+BKDNS` | `1,0,3.127.172.15,1883` | Cached/fallback IP = same HiveMQ IP |
| `AT+UNAME` | `7donD0lgPwI5aJcS83dS` | Looks like a **ThingsBoard device access token** |
| `AT+PWD` | `NULL` | Typical for TB token-as-username |
| `AT+PUBTOPIC` | `v1/devices/me/telemetry` | ThingsBoard MQTT telemetry topic |
| `AT+SUBTOPIC` | `#` | Broad subscribe (risky / odd on public brokers) |
| `AT+CLIENT` | `null` | |
| `AT+TLSMOD` | `0,0` | No TLS |
| `AT+MQOS` | `1` | |
| `AT+TDC` | `180` | **3 minutes** (set 2026-08-06; was `120`) |
| `AT+APN` | `lpwa.vodafone.is` | |

Earlier CFG dump (2026-08-05) already had `SERVADDR=broker.hivemq.com,1883` and `BKDNS=1,0,18.198.118.51,1883` (also a HiveMQ resolution). So this unit has been on HiveMQ across sessions in this workspace.

### Mismatch (root of “fail of data” toward ThingsBoard)

The device is configured as a **ThingsBoard MQTT device** (token + `v1/devices/me/telemetry`) but pointed at **HiveMQ public broker**. HiveMQ will accept an anonymous/open MQTT session and may ACK a publish (`Upload data successfully`), then a later step fails (`Failed to send`) — often subscribe/second publish/QoS path. Data never reaches the private ThingsBoard.

## What the official manual says (evidence)

> Workspace PDF under `PS-CB-NA/manuals/` is a **9 KB stub** (no usable text). Use the companion export **`PS-CB-NA/manuals/PPS-CB-NA - NB-IoT_LTE-M Analog Sensor.md`** and the structured extract **`PS-CB-NA/manuals/PPS-CB-NA_NB-IoT_LTE-M_Analog_Sensor.md`**. Serial steps: **`AT_COMMANDS_HANDOFF.md`** (archive flag: `archive/research/MANUAL_HANDOFF_READY.txt`). Supplemental: Dragino Docs PS-CB / PS-CB-NA + shared NB-IoT stack (`AT+BKDNS`, HiveMQ SERVADDR example).

Sources:

- Workspace: [PPS-CB-NA companion MD](../PS-CB-NA/manuals/PPS-CB-NA%20-%20NB-IoT_LTE-M%20Analog%20Sensor.md) · [structured extract](../PS-CB-NA/manuals/PPS-CB-NA_NB-IoT_LTE-M_Analog_Sensor.md) · [AT handoff](AT_COMMANDS_HANDOFF.md)
- https://docs.dragino.com/docs/NB-IoT/flow-pressure-weight-sensors/ps-cb/
- https://docs.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/
- https://docs.dragino.com/docs/NB-IoT/general-configuration/nb-ns-models-nb-iot/

### 1) `AT+PRO=3,3` rewrites server-related defaults (primary trigger)

From PS-CB / PS-CB-NA payload docs:

- `AT+PRO=3,3` → **MQTT Connection & ThingsBoard**
- **ThingsBoard Payload (Type=3):**  
  *“Type3 payload special design for ThingsBoard, **it will also configure other default server to ThingsBoard**.”*

So selecting / re-applying **ThingsBoard mode via `AT+PRO=3,3` is documented to change more than payload format** — it also pushes **default server** settings. That is the strongest documented explanation for “I changed a setting and the MQTT server jumped.”

Related profile switches that also rewrite platform defaults:

| Command | Effect (manual) |
|---|---|
| `AT+PRO=3,0` | MQTT + hex |
| `AT+PRO=3,1` | MQTT + ThingSpeak (platform defaults) |
| `AT+PRO=3,3` | MQTT + ThingsBoard (**also configures default server**) |
| `AT+PRO=3,5` | MQTT + JSON |
| `AT+PRO=2,x` / `4,x` | UDP / TCP profiles |

**Practical rule:** After any `AT+PRO=…` change, **immediately re-check `AT+SERVADDR=?` / `AT+CFG`**. Re-set private ThingsBoard host if it was overwritten.

### 2) Factory reset restores canned defaults

From PS-CB AT list:

- `AT+FDR` — Reset parameters to factory default  
- `AT+FDR1` — Factory default **except passwords**

Factory / “default IoT server connection” SKUs are designed to work with simple out-of-box server settings. Combined with (3), factory MQTT demo host is HiveMQ.

### 3) `broker.hivemq.com` is Dragino’s documented MQTT address example

Shared NB-IoT stack changelog (Format `AT+SERVADDR` command) uses exactly:

```text
AT+SERVADDR=broker.hivemq.com,1883
```

(as the example of writing the server address, including stripping spaces). That is strong evidence HiveMQ is the **stock/demo MQTT broker** baked into docs and typical factory MQTT config — not the user’s private ThingsBoard.

### 4) `AT+BKDNS` caches the resolved IP (secondary / sticky failover)

From NB stack docs (`AT+BKDNS`):

- `a=1`: dynamic domain update disabled; **IP saved after resolve**; if later DNS fails, **last saved IP is used**
- `a=2`: dynamic update enabled on an interval (hours); parameter `c` is updated to the resolved IP on success
- Default query example: `1,0,NULL`

On this device: `AT+BKDNS=1,0,3.127.172.15,1883` — i.e. **HiveMQ’s IP is cached as fallback**.

Even after you set `AT+SERVADDR=<private-tb-host>,1883`, if DNS to the private host fails, the module can still dial the **old HiveMQ IP**. That looks like “it switched back to HiveMQTT” even when the hostname string was updated.

**Fix when pointing at private TB:** clear/replace BKDNS, e.g. disable cache or set `c` to the private server IP:

```text
AT+BKDNS=1,0
```

or after SERVADDR is correct and resolves once, verify `AT+BKDNS=?` no longer holds a HiveMQ address.

### 5) Commands that set MQTT host (do these deliberately)

| Command | Role |
|---|---|
| `AT+SERVADDR=<host>,<port>` | Primary MQTT/UDP/TCP server |
| `AT+BKDNS=…` | DNS cache / failover IP (same host:port shape as SERVADDR) |
| `AT+UNAME` / `AT+PWD` / `AT+CLIENT` | MQTT auth |
| `AT+PUBTOPIC` / `AT+SUBTOPIC` | MQTT topics |
| `AT+PRO=<proto>,<payloadType>` | Protocol + payload profile (**may rewrite defaults**) |
| `AT+TLSMOD` / `AT+MQOS` | TLS / QoS |
| `AT+CFG` | Dump all settings (detection) |
| `AT+FDR` / `AT+FDR1` | Factory wipe of parameters |

ThingsBoard private MQTT (access-token style) typically needs:

```text
AT+PRO=3,3
AT+SERVADDR=<YOUR_TB_HOST>,1883
AT+UNAME=<DEVICE_ACCESS_TOKEN>
AT+PWD=NULL
AT+PUBTOPIC=v1/devices/me/telemetry
AT+SUBTOPIC=v1/devices/me/attributes   # avoid '#' on public brokers
AT+BKDNS=1,0                            # then verify IP is TB, not HiveMQ
AT+TDC=180
```

(Use `8883` + TLS if the private server requires it — currently `TLSMOD=0,0`.)

Manual note: some Dragino “ThingsBoard.Cloud via MQTT” examples use integration-style `PUBTOPIC`/`UNAME` = device name; this unit uses the **token + `v1/devices/me/telemetry`** pattern instead. Either way, **SERVADDR must be the private TB broker**, not HiveMQ.

## Which setting flips the broker to HiveMQ? (verdict)

| Rank | Setting / action | Confidence | Why |
|---|---|---|---|
| 1 | **`AT+PRO=3,3` (or other `AT+PRO=…` profile changes)** | **High** | Manual: Type3 “**will also configure other default server**”. Easy to hit when “switching to ThingsBoard mode”. |
| 2 | **`AT+FDR` / `AT+FDR1`** | **High** | Explicit factory default restore; demo MQTT host is HiveMQ. |
| 3 | **`AT+BKDNS` leftover HiveMQ IP after SERVADDR change** | **Medium–High** | Documented failover to last resolved IP; explains “still talking to HiveMQ” after hostname change. |
| 4 | **`AT+TDC` / GPS (`AT+GPS`/`AT+GTDC`)** | **Low (ruled out as hostname rewrite)** | We set `AT+TDC=180` while unlocked; CFG still `broker.hivemq.com` before and after — TDC does **not** rewrite SERVADDR. GPS enable sessions also showed HiveMQ already present. |
| 5 | Accidental `AT+SERVADDR=broker.hivemq.com,1883` | Possible | Same string is the manual’s example address. |

**Best single answer for “which setting triggers it”:** changing **`AT+PRO`** into ThingsBoard/MQTT profiles (especially **`AT+PRO=3,3`**), or running a **factory reset (`AT+FDR`/`AT+FDR1`)**. Sticky **`AT+BKDNS`** HiveMQ IPs amplify the problem after you try to fix `SERVADDR`.

We did **not** re-run `AT+PRO` or `AT+FDR` on the live unit (would be destructive / would confirm by breaking private TB again). Confidence is from manual wording + live CFG mismatch + serial upload behavior.

## How to detect

1. Unlock AT console (6-digit PIN / `AT+PIN=…`).
2. `AT+SERVADDR=?` and/or `AT+CFG`.
3. Fail if host contains `hivemq` or resolves to unexpected public IPs.
4. Also check `AT+BKDNS=?` — if IP is HiveMQ while SERVADDR claims private TB, failover is still wrong.
5. In debug uplink logs: `Domain IP:…,1883` / `Successfully connected to the server` then `Failed to send`.

## How to fix (non-destructive checklist)

1. Set private TB host: `AT+SERVADDR=<private-host>,1883` (or correct TLS port).
2. Fix BKDNS so it cannot fall back to HiveMQ: `AT+BKDNS=1,0` then re-check after one successful resolve; or set `c` to the private IP.
3. Keep auth/topics aligned with your TB setup (token vs integration).
4. Prefer a narrow `AT+SUBTOPIC` (not `#`) on shared brokers.
5. Confirm: `AT+CFG` → SERVADDR + BKDNS both private-TB, not `broker.hivemq.com`.
6. After any future `AT+PRO=…`, repeat steps 1–5.

**Do not** use `AT+FDR` unless you intend a full parameter wipe.

## Cycle time (3 minutes) — status

| When | `AT+TDC` |
|---|---|
| Before (2026-08-05 CFG) | `120` (2 min) |
| After (2026-08-06) | **`180` (3 min)** — `AT+TDC=180` → readback `180` / `OK`; CFG shows `AT+TDC=180` |

Command used: `AT+TDC=180` (seconds, per PS-CB manual / project README).

## Fail-of-data diagnosis (ThingsBoard)

Observed pattern on COM8:

```text
Opened the MQTT client network successfully
Successfully connected to the server
Upload data successfully
Failed to send
*****End of upload*****
```

Most likely causes **on this unit**:

1. **Wrong broker** — publishing TB telemetry to **HiveMQ** (`SERVADDR=broker.hivemq.com`). Primary issue.
2. **`SUBTOPIC=#`** — wide subscribe on a public broker; matches “first publish OK, later send fails” pattern.
3. Secondary: QoS1 / dual publish path in firmware after a partial success.

Network itself is fine (eMTC attach, CSQ ~23, DNS works). This is an **application/MQTT endpoint mismatch**, not radio failure.

## Serial evidence logs

- `logs/20260806_112527_ps_serial.raw.log` — unlock, CFG, `TDC=180`, fail-of-data
- `logs/20260806_114235_hivemq_probe.raw.log` — `SERVADDR=?`, `BKDNS=?`, confirm TDC 180
- `logs/20260805_161246_gps.raw.log` — earlier full CFG already on HiveMQ

## Quick reference commands

```text
357319                 # or AT+PIN=357319 — unlock
AT+CFG                 # full dump
AT+SERVADDR=?
AT+BKDNS=?
AT+PRO=?
AT+TDC=?
AT+TDC=180             # 3-minute uplink
```

---

**Bottom line:** The broker flips to HiveMQ because Dragino’s MQTT **factory/demo host** is `broker.hivemq.com`, and **`AT+PRO=3,3` (ThingsBoard payload mode) is documented to reconfigure the default server**. Factory reset does the same. `AT+BKDNS` can keep dialing HiveMQ IPs even after you change the hostname. Re-set `SERVADDR` + clear HiveMQ from `BKDNS` after every `AT+PRO` change; keep `TDC=180` as already applied.
