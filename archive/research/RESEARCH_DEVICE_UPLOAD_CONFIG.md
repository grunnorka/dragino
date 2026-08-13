# Research: PPS-CB-NA upload / MQTT / ThingsBoard config

**Question:** How to set correct settings on the Dragino **PPS-CB-NA** (NB-IoT/LTE-M Analog Sensor) for upload, MQTT, and ThingsBoard — practical order of operations, AT commands, and pitfalls.

**Product name note:** Official docs brand the product **PS-CB-NA**; workspace files also use **PPS-CB-NA**. Same device family. ([wiki.dragino.com PS-CB-NA](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/); workspace companion MD title)

**This note does not change device config.** Research + cite only.

---

## Sources

| Priority | Source | Role |
|---|---|---|
| Primary | [PS-CB-NA wiki](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/) · [docs.dragino.com mirror](https://docs.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/) | Product manual (payload, PRO, TDC, CLOCKLOG, PIN, PROBE) |
| Primary | Workspace companion: `PPS-CB-NA – NB-IoT_LTE-M Analog Sensor.md` | User-provided full manual export (PDF stub on disk is unusable) |
| Primary | [General Configure for -CB & -CS](https://wiki.dragino.com/docs/NB-IoT/general-configuration-manual/general-configure-for-cb-cs-models-nb-iot-lte-m/) | MQTT profile, ThingsBoard.Cloud, SERVADDR/HiveMQ example, MQOS, TLSMOD |
| Primary | [NB/NS models — BKDNS](https://wiki.dragino.com/docs/NB-IoT/general-configuration/nb-ns-models-nb-iot/) | `AT+BKDNS` semantics (same stack family) |
| Structured extract | `PPS-CB-NA_NB-IoT_LTE-M_Analog_Sensor.md` | Condensed primary extract |
| Secondary (workspace verified) | `AT_COMMANDS_HANDOFF.md`, `HIVEMQ_BROKER_SWITCH.md`, `TELEMETRY_8_SLOTS.md`, serial logs under `logs/` | Live CFG / fail patterns on this unit — labeled where used |

---

## 1. Serial access / PIN / CFG read

### Access methods

PS-CB-NA supports AT via **BLE** (recommended) or **UART**. ([PS-CB-NA §3.1](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/); companion MD §3.1)

Workspace UART practice: **COM8 @ 9600 8N1**, SW1 = Flash. ([README.md](README.md) — workspace verified)

### Unlock

After BLE/UART connect, enter the AT console with the **serial access password** printed on the box label:

```text
AT+PIN=xxxxxx
```

or type the six digits alone + newline. Change password with `AT+PWORD=xxxxxx` (6 characters; CB nodes: lowercase only). Every command needs a **trailing newline**. ([PS-CB-NA §3.2](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/); companion MD §3.2)

Store PIN in gitignored `.env` as `DRAGINO_PIN` — do not commit it. ([README.md](README.md) — workspace verified)

### Dump / query

```text
AT+CFG                 # print all settings (product AT list)
AT+SERVADDR=?
AT+PRO=?
AT+TDC=?
AT+CLOCKLOG=?
AT+UNAME=?
AT+PUBTOPIC=?
AT+SUBTOPIC=?
AT+BKDNS=?             # stack command; present on this firmware family
```

`AT+CFG` / `AT+SERVADDR` / `AT+TDC` / `AT+PRO` / MQTT commands are in the product AT list. ([PS-CB-NA §3.3](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/))  
`AT+BKDNS` is documented on the shared NB stack (NB/NS + CB changelog references), not always in the short PS-CB-NA AT table. ([NB/NS §7.1.4 Domain name resolution](https://wiki.dragino.com/docs/NB-IoT/general-configuration/nb-ns-models-nb-iot/); CB general config changelog “Related Command: AT+BKDNS”)

---

## 2. ThingsBoard MQTT profile (what to set)

### Protocol + payload

| Command | Meaning |
|---|---|
| `AT+PRO=3,3` | Protocol **3** = MQTT; payload type **3** = ThingsBoard |

Other MQTT payload types from the same table: `3,0` hex · `3,1` ThingSpeak · `3,5` JSON. ([PS-CB-NA §2.2 / wiki AT+PRO list](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/))

**Critical (primary):**

> Type3 payload special design for ThingsBoard, **it will also configure other default server to ThingsBoard.**

([PS-CB-NA §2.2.3 ThingsBoard Payload(Type=3)](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/); companion MD §2.2.3)

### Two ThingsBoard MQTT patterns

**A) Dragino ThingsBoard.Cloud via MQTT integration** (official CB general config §3.6): device-name style topics/credentials after `AT+PRO=3,3`, then set server to the integration MQTT host. ([CB & CS general configure §3.6](https://wiki.dragino.com/docs/NB-IoT/general-configuration-manual/general-configure-for-cb-cs-models-nb-iot-lte-m/))

**B) Private / self-hosted ThingsBoard — device access token** (common TB MQTT device API; used on this workspace unit):

| Setting | Typical value | Notes |
|---|---|---|
| `AT+PRO` | `3,3` | ThingsBoard payload |
| `AT+SERVADDR` | `<TB_HOST>,1883` | Or `8883` if TLS |
| `AT+UNAME` | `<DEVICE_ACCESS_TOKEN>` | Token as MQTT username |
| `AT+PWD` | `NULL` | Typical for token-as-username |
| `AT+PUBTOPIC` | `v1/devices/me/telemetry` | TB device telemetry topic |
| `AT+SUBTOPIC` | `v1/devices/me/attributes` | Prefer narrow topic; avoid `#` on shared brokers |
| `AT+CLIENT` | `null` | Optional |
| `AT+MQOS` | `0` / `1` / `2` | Stack default often **0**; `1` = at-least-once ([CB general § MQOS](https://wiki.dragino.com/docs/NB-IoT/general-configuration-manual/general-configure-for-cb-cs-models-nb-iot-lte-m/)) |
| `AT+TLSMOD` | `0,0` plain · or TLS per broker (e.g. docs show `1,0` / `1,2` for other platforms) | Product AT: Get/Set TLS mode ([PS-CB-NA §3.3](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/)) |

Token + `v1/devices/me/telemetry` pattern: **workspace verified** on this unit (`HIVEMQ_BROKER_SWITCH.md`, `AT_COMMANDS_HANDOFF.md`). Official Dragino §3.6 uses integration-style device-name topics — pick one mode and keep SERVADDR/topics/auth consistent.

### General MQTT command shapes (official)

From CB general MQTT connection:

```text
AT+PRO=3,0
AT+SERVADDR=<host>,<port>
AT+CLIENT=...
AT+UNAME=...
AT+PWD=...
AT+PUBTOPIC=...
AT+SUBTOPIC=...
```

([CB & CS general configure §3.2](https://wiki.dragino.com/docs/NB-IoT/general-configuration-manual/general-configure-for-cb-cs-models-nb-iot-lte-m/))

---

## 3. Order of operations (HiveMQ rewrite)

### Required order when enabling ThingsBoard mode

```text
1. Unlock (AT+PIN / six digits)
2. AT+PRO=3,3                         # last if you must set it — rewrites default server
3. AT+SERVADDR=<YOUR_TB_HOST>,1883    # ALWAYS re-set AFTER PRO
4. AT+UNAME / AT+PWD / topics / MQOS / TLSMOD
5. AT+BKDNS=1,0                       # clear sticky HiveMQ IP (see §4)
6. AT+TDC=... and AT+CLOCKLOG=...     # upload vs sample (see §5)
7. AT+CFG                             # verify — no broker.hivemq.com
```

**Why SERVADDR after PRO:** Type=3 “will also configure other default server to ThingsBoard.” ([PS-CB-NA §2.2.3](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/))

**Why HiveMQ appears:** Dragino NB stack changelog uses exactly `AT+SERVADDR=broker.hivemq.com,1883` as the Format SERVADDR example (spaces stripped). That is the documented demo MQTT host string. ([CB general §8.1.2 Format AT+SERVADDR](https://wiki.dragino.com/docs/NB-IoT/general-configuration-manual/general-configure-for-cb-cs-models-nb-iot-lte-m/); [NB/NS same example](https://wiki.dragino.com/docs/NB-IoT/general-configuration/nb-ns-models-nb-iot/))

**Factory wipe:** `AT+FDR` / `AT+FDR1` restore factory defaults (FDR1 except passwords) — do not use casually. ([PS-CB-NA AT list](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/))

Copy-paste template (placeholders only — **do not apply from this research note alone**):

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
AT+CLOCKLOG=1,65535,5,8
AT+TDC=1800
AT+CFG
```

([AT_COMMANDS_HANDOFF.md](AT_COMMANDS_HANDOFF.md) — workspace verified command shapes; intervals per §5 below)

---

## 4. BKDNS sticky IP

`AT+BKDNS=a,b,c` (shared NB stack):

| Param | Meaning |
|---|---|
| `a=1` | Dynamic DNS update **disabled**; IP saved after resolve; if later DNS fails, **last saved IP is used** |
| `a=2` | Dynamic update **enabled**; refresh every `b` hours |
| `b` | Update interval (hours) |
| `c` | Manual/cached IP, same shape as `SERVADDR` (`ip,port`); on successful resolve, `c` is updated |

Examples from docs: `AT+BKDNS=1,0` · `AT+BKDNS=2,1` · `AT+BKDNS=2,4,3.69.98.183,1883`. Default query example often `1,0,NULL`. ([NB/NS Domain name resolution](https://wiki.dragino.com/docs/NB-IoT/general-configuration/nb-ns-models-nb-iot/); PS-NB/NS user manual AT+BKDNS section)

**Pitfall:** After the device once resolved `broker.hivemq.com`, `c` may hold a public HiveMQ IP. Changing only `SERVADDR` to a private hostname can still dial HiveMQ when DNS to the private host fails. ([HIVEMQ_BROKER_SWITCH.md](HIVEMQ_BROKER_SWITCH.md) — workspace verified: live `AT+BKDNS=1,0,3.127.172.15,1883` / `18.198…` while topics still looked ThingsBoard)

**Fix:** After setting private SERVADDR, run `AT+BKDNS=1,0` (or set `c` to the private IP once known) and confirm `AT+BKDNS=?` has **no** HiveMQ address.

---

## 5. TDC (upload) vs CLOCKLOG (sample + history slots)

These are **independent timers**.

| Setting | Units | Role |
|---|---|---|
| `AT+TDC=<seconds>` | **Seconds** | How often the device **publishes** (application uplink interval). Manual example default path: every **2 hours** until changed (`AT+TDC=7200`). ([PS-CB-NA §3.4 / AT+TDC](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/)) |
| `AT+CLOCKLOG=a,b,c,d` | `c` in **minutes** | On-device **sample** interval + how many history entries ride each TDC uplink |

### CLOCKLOG parameters ([PS-CB-NA §3.9](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/))

| Param | Meaning |
|---|---|
| `a` | `0` disable · `1` enable |
| `b` | First sample start second `0–3599`, or **`65535`** = start after first network/uplink packet |
| `c` | Sampling interval **`0–255` minutes** |
| `d` | How many history entries in each TDC uplink (**max 32**) |

Disable logging: `AT+CLOCKLOG=1,65535,0,0`.  
Example: `AT+CLOCKLOG=1,65535,1,3` → sample every **1 min**, uplink last **3** records.  
Downlink example `0301FFFF0F08` → `AT+CLOCKLOG=1,65535,15,8`.  

**Note:** Sync server time before configuring CLOCKLOG; otherwise it may take effect only after node reset. ([PS-CB-NA §3.9](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/))

JSON keys `"1"`…`"d"` are the last `d` samples: each `[Idc_input, Vdc_input, Sampling time]`. Live fields (`idc_input`, `vdc_input`, `battery`, `signal`, `time`, …) are values **at uplink time**. ([PS-CB-NA §2.2.1 notice](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/); [TELEMETRY_8_SLOTS.md](TELEMETRY_8_SLOTS.md))

**Do not confuse with `AT+STDC`:** multi-acquire then one uplink for IDC **or** VDC — different feature. ([PS-CB-NA §3.8](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/))

### Mental model

```text
Analog IDC/VDC
      │  every c minutes (CLOCKLOG)
      ▼
On-device history ≤32
      │  every TDC seconds
      ▼
One MQTT JSON: live fields + "1"…"d"
      │
      ▼
ThingsBoard
```

---

## 6. Recommended values: sample 5 min, upload 30 min

| Goal | Command | Why |
|---|---|---|
| Sample every **5 minutes** | `AT+CLOCKLOG=1,65535,5,8` | `c=5` minutes ([§3.9](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/)); `d=8` keeps ~40 min of history per uplink (5×8) |
| Upload every **30 minutes** | `AT+TDC=1800` | `1800` seconds = 30 min ([§3.4 TDC in seconds](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/)) |

Minimum `d` to cover one full 30-minute window at 5-minute sampling: **6** (`30/5`). Using **`d=8`** matches the manual’s common example shape and leaves headroom. ([§2.2.1 / §3.9 examples with `d=8`](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/))

**Workspace verified (already applied on this unit in a later session):** serial log shows set + readback `AT+CLOCKLOG=1,65535,5,8` and `AT+TDC=1800` (`logs/20260806_130220_tdc_clocklog.raw.log`). Earlier sessions had `CLOCKLOG=…,15,8` and `TDC=180` (3 min) — different target.

---

## 7. What telemetry looks like (mA, not mm on device)

### Payload fields

Type=3 / JSON examples publish **`idc_input` / `vdc_input` in engineering units of the ADC channels** (mA / V), plus battery, signal, time, optional GPS, and CLOCKLOG keys `"1"`…`"8"`. ([PS-CB-NA §2.2.1 / §2.2.3](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/))

IDC measuring range on the product: **0–20 mA** (accuracy 0.02 mA). ([PS-CB-NA §1.3](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/))

HEX decode example treats 0–20 mA as raw/1000 → mA (e.g. `27AE` → 10.158 mA). ([PS-CB-NA HEX payload §](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/))

**No on-device millimetre field** in the documented JSON/ThingsBoard examples — values stay as `idc_input` (mA). ([§2.2.1 / §2.2.3 examples](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/); live LDATA in logs shows `"idc_input":4.03…` — workspace verified)

### Probe model (`AT+PROBE`) — server hint, not a mm telemetry key

Manual: 4–20 mA is full scale of the attached probe; different probes mean different engineering units. User sets probe model so **the IoT server can decode** current/voltage into depth or pressure. ([PS-CB-NA Probe Model / §3.7](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/))

Water depth mode: `AT+PROBE=aabb` with `aa=00`, `bb` = probe span in **metres** (e.g. `0003` = 3 m, `000A` = 10 m). ([§3.7](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/))

For a **0–1000 mm (1 m)** 4–20 mA level probe, optional device hint: `AT+PROBE=0001`. Conversion to mm for dashboards is still done in **ThingsBoard** (calculated telemetry / rule chain), because published keys remain mA-shaped.

### ThingsBoard formula: 4–20 mA → 0–1000 mm

Linear map (primary physics of 4–20 mA full-scale span; probe table states 4–20 mA = full measuring range):

\[
\text{depth\_mm} = \frac{\text{idc\_input} - 4}{20 - 4} \times 1000 = (\text{idc\_input} - 4) \times 62.5
\]

ThingsBoard calculated telemetry (example expression style):

```text
(idc_input - 4) * 62.5
```

Clamp if desired: below 4 mA → 0; above 20 mA → 1000.  
Same formula applies to CLOCKLOG array element `[0]` if you unpack history keys `"1"`…`"8"`.

---

## 8. Verify checklist after config

Run after unlock:

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
AT+CLOCKLOG=?
AT+APN=?
```

**Pass criteria**

| Check | Pass if |
|---|---|
| Broker | `SERVADDR` = private TB host — **not** `broker.hivemq.com` |
| Sticky DNS | `BKDNS` has **no** HiveMQ public IP |
| Profile | `PRO=3,3` if ThingsBoard Type=3 desired |
| Auth/topics | Match chosen TB mode (token+`v1/devices/me/telemetry` **or** integration device-name) |
| Upload | `TDC=1800` for 30 min target |
| Sample | `CLOCKLOG=1,65535,5,…` for 5 min sample (`d` ≥ 6, typically 8) |
| Radio | Valid `APN` for SIM; network attach before expecting MQTT |

Then confirm in ThingsBoard: telemetry arrives on the device; `idc_input` updates; calculated mm (if configured) tracks 4–20 mA. Optional serial: button uplink 1–3 s or wait for next TDC; watch for `Upload data successfully` **without** following `Failed to send` on the wrong broker. ([HIVEMQ_BROKER_SWITCH.md](HIVEMQ_BROKER_SWITCH.md) — workspace verified failure pattern)

---

## 9. Common failures

| Symptom | Likely cause | Evidence |
|---|---|---|
| `Upload data successfully` then **`Failed to send`** | Publishing ThingsBoard-shaped topics/token to **wrong broker** (esp. public HiveMQ); wide `SUBTOPIC=#` can worsen post-publish failure | Workspace serial pattern + diagnosis ([HIVEMQ_BROKER_SWITCH.md](HIVEMQ_BROKER_SWITCH.md); e.g. `logs/20260806_123854_cfg_verify.raw.log`) |
| SERVADDR suddenly `broker.hivemq.com` | Re-applied `AT+PRO=3,3` (default server rewrite) or `AT+FDR`/`AT+FDR1` | [§2.2.3](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/); [SERVADDR example](https://wiki.dragino.com/docs/NB-IoT/general-configuration-manual/general-configure-for-cb-cs-models-nb-iot-lte-m/) |
| Hostname fixed but still dials HiveMQ IP | `AT+BKDNS` still caches HiveMQ IP; DNS to private host fails → fallback | [BKDNS docs](https://wiki.dragino.com/docs/NB-IoT/general-configuration/nb-ns-models-nb-iot/); workspace CFG |
| TB “Last update” every few minutes but sample times ~15/5 min apart | Normal: TDC republishes; CLOCKLOG `c` sets inner spacing | [§2.2.1 / §3.9](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/); [TELEMETRY_8_SLOTS.md](TELEMETRY_8_SLOTS.md) |
| Expecting mm in raw telemetry | Device sends **mA**; mm is server-side (and/or PROBE as decode hint) | [§2.2 / §3.7](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/) |
| CLOCKLOG not sampling as set | Time not synced before config → takes effect after reset | [§3.9 note](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/) |
| No MQTT at all | Wrong APN / no attach / TLS mismatch (`TLSMOD` vs port) | Product APN/TLS AT list; CB general MQTT power/interval notice |

---

## 10. Quick reference (AT cheat sheet)

```text
# Unlock
AT+PIN=<6-digit>          # or digits alone + newline

# ThingsBoard MQTT (token style) — SERVADDR AFTER PRO
AT+PRO=3,3
AT+SERVADDR=<TB_HOST>,1883
AT+UNAME=<TOKEN>
AT+PWD=NULL
AT+PUBTOPIC=v1/devices/me/telemetry
AT+SUBTOPIC=v1/devices/me/attributes
AT+MQOS=1
AT+TLSMOD=0,0
AT+BKDNS=1,0

# Sample 5 min / upload 30 min
AT+CLOCKLOG=1,65535,5,8
AT+TDC=1800

# Verify
AT+CFG
AT+SERVADDR=?
AT+BKDNS=?
AT+CLOCKLOG=?
AT+TDC=?
```

Do **not** run `AT+FDR` / `AT+FDR1` unless a full wipe is intended.

---

## Bottom line

1. Unlock with box PIN; dump with `AT+CFG`.  
2. For ThingsBoard: `AT+PRO=3,3`, then **immediately** re-set private `AT+SERVADDR` and clear **`AT+BKDNS`** (HiveMQ demo host + sticky IP are the main broker pitfalls).  
3. **`AT+TDC`** = upload period in **seconds**; **`AT+CLOCKLOG` `c`** = sample period in **minutes**; for 5 min / 30 min use `CLOCKLOG=1,65535,5,8` and `TDC=1800`.  
4. Telemetry is **`idc_input` in mA**; map 4–20 mA → 0–1000 mm in ThingsBoard with `(idc_input - 4) * 62.5`.  
5. After config, verify SERVADDR/BKDNS are private TB, then confirm TB receives publishes (and that serial does not show HiveMQ + `Failed to send`).
