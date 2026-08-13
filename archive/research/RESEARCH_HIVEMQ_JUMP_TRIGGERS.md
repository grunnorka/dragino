# Research: What rewrites PS-CB-NA MQTT `SERVADDR` to HiveMQ?

**Date:** 2026-08-07  
**Device under discussion:** Dragino PS-CB-NA / PS-CB, firmware **v1.1.4**, stack **D-BG95-003**  
**Question:** What settings / AT commands / events cause (or appear to cause) `AT+SERVADDR` to become `broker.hivemq.com,1883`, and how do `AT+BKDNS`, `AT+PRO` Type=3, factory reset, and related stack behavior interact?

**Method:** Claims below are tied to Dragino first-party docs / workspace manuals / live serial logs. Workspace notes (`HIVEMQ_BROKER_SWITCH.md`, `AT_COMMANDS_HANDOFF.md`) were used as leads only; wording was re-checked against primary sources.

---

## 1. Executive summary (ranked)

| Rank | Trigger / mechanism | Effect on broker | Confidence that it explains **hostname** `broker.hivemq.com` |
|---|---|---|---|
| **1** | Explicit write of `AT+SERVADDR=broker.hivemq.com,1883` (serial, Bluetooth config, or MQTT/JSON downlink) | Directly sets hostname to HiveMQ | **High** — this exact string is Dragino’s documented SERVADDR example |
| **2** | Sticky `AT+BKDNS` failover to a previously resolved HiveMQ IP | Device **dials HiveMQ IP** even if `SERVADDR` was changed to a private host (DNS fail path) | **High** for “talks to HiveMQ”; **does not by itself rewrite** the SERVADDR hostname string |
| **3** | MQTT subscribe downlink while `SUBTOPIC=#` on a reachable broker (especially public HiveMQ) | Remote `{"Config":"[AT+SERVADDR=…;ATZ]"}` can rewrite server | **Medium–High** as a capability; **unproven** for this unit’s historical jump |
| **4** | `AT+FDR` / `AT+FDR1` factory parameter restore | Restores factory parameters (host unknown for this SKU) | **High** that FDR wipes to factory; **Low–Medium** that factory host is HiveMQ on PS-CB-NA (docs: GE = no IoT server; 1T = ThingsEye) |
| **5** | `AT+PRO=3,3` ThingsBoard payload Type=3 | Manual: “configure other default server **to ThingsBoard**” (not HiveMQ) | **Medium** that Type=3 rewrites *some* server-related defaults after `ATZ`; **Low** that it specifically writes `broker.hivemq.com` (docs say ThingsBoard; live re-apply without `ATZ` did **not** change SERVADDR) |
| **6** | Accidental / scripted copy of docs example | Same as (1) | **Medium** — HiveMQ string is ubiquitous in Dragino changelogs |
| **—** | `AT+TDC` interval changes | No documented SERVADDR side effect | **Ruled out** as hostname rewrite (docs silent; live tests kept private host) |

**Bottom line:** Official docs **do not** state that the PS-CB-NA factory MQTT host is HiveMQ, nor that `AT+PRO=3,3` writes `broker.hivemq.com`. They **do** document (a) HiveMQ as the canonical `AT+SERVADDR` example, (b) Type=3 “also configure other default server to ThingsBoard,” (c) `AT+FDR`/`AT+FDR1` factory restore, (d) `AT+BKDNS` last-resolved-IP failover, and (e) JSON downlink that can set `AT+SERVADDR` + `ATZ`. On this live unit, `SERVADDR=broker.hivemq.com` has been observed repeatedly with `PRO=3,3` and BKDNS holding a HiveMQ IP; a controlled re-apply of `AT+PRO=3,3` / `3,5` **without** a successful `ATZ` did **not** flip a restored private host back to HiveMQ.

---

## 2. Evidence table

| Claim | Confidence | Primary source | Quote / observation |
|---|---|---|---|
| Type=3 ThingsBoard payload “will also configure other default server to ThingsBoard” | **High** (wording); **Low–Medium** (exact fields rewritten; host not named HiveMQ) | [PS-CB-NA docs §2.2.3](https://docs.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/); same on [PS-CB §2.2.3](https://docs.dragino.com/docs/NB-IoT/flow-pressure-weight-sensors/ps-cb/); workspace `PPS-CB-NA – NB-IoT_LTE-M Analog Sensor.md` | “Type3 payload special design for ThingsBoard, it will also configure other default server to ThingsBoard.” |
| `AT+PRO=3,3` selects MQTT + ThingsBoard payload | **High** | Same PS-CB-NA / PS-CB payload sections | `AT+PRO=3,3 // Use MQTT Connection & ThingsBoard` |
| ThingsBoard MQTT setup uses `AT+PRO=3,3` then topics/auth; SERVADDR set separately in examples | **High** | [CB/CS models §3.6](https://docs.dragino.com/docs/NB-IoT/general-configuration/cb-cs-models-nb-iot-lte-m/); [NB/NS §3.7](https://docs.dragino.com/docs/NB-IoT/general-configuration/nb-ns-models-nb-iot/) | `AT+PRO=3,3 // Use MQTT to connect to ThingsBoard. Payload Type set to 3.` then `AT+SUBTOPIC` / `AT+PUBTOPIC` / `AT+UNAME` / `AT+PWD` (device-name style). No line saying PRO alone sets `broker.hivemq.com`. |
| HiveMQ appears as Dragino’s SERVADDR formatting example | **High** | [NB/NS §7.1.2](https://docs.dragino.com/docs/NB-IoT/general-configuration/nb-ns-models-nb-iot/); [CB/CS §8.1.2](https://docs.dragino.com/docs/NB-IoT/general-configuration/cb-cs-models-nb-iot-lte-m/) | Example ends as: `AT+SERVADDR=broker.hivemq.com,1883` |
| `AT+FDR` / `AT+FDR1` restore factory parameters | **High** | PS-CB-NA AT list; NB/NS password/FDR section | `AT+FDR // Reset Parameters to Factory Default.` / `AT+FDR1 // … except for passwords.` |
| PS-CB-NA GE version does **not** ship pointed at an IoT server | **High** | [PS-CB-NA §2.1](https://docs.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/) | “GE Version: This version doesn't include SIM card or point to any IoT server.” |
| PS-CB-NA 1T version is preconfigured to **ThingsEye** (not HiveMQ) | **High** | Same §2.1 | “1T Version: … configure to send value to ThingsEye.” |
| `AT+BKDNS` saves resolved IP and uses last saved IP if next DNS fails | **High** | [NB/NS §7.1.4 Domain name resolution](https://docs.dragino.com/docs/NB-IoT/general-configuration/nb-ns-models-nb-iot/) | “The ip address will be saved after the domain name is resolved, if the next domain name resolution fails, the last saved ip address will be used.” Default query example: `1,0,NULL` |
| CB/CS BG95 stack changelog references `AT+BKDNS` | **High** | [CB/CS §8.3.5](https://docs.dragino.com/docs/NB-IoT/general-configuration/cb-cs-models-nb-iot-lte-m/) | “Related Command: `AT+BKDNS` (enhanced for better error handling)” |
| Live PS-CB-NA (D-BG95-003) implements `AT+BKDNS` | **High** | Serial CFG dumps in workspace logs | e.g. `AT+BKDNS=1,0,52.59.36.109,1883` with `SERVADDR=broker.hivemq.com,1883` |
| JSON downlink can set `AT+SERVADDR` and requires `ATZ` for server/protocol changes | **High** | [CB/CS §8.2.7](https://docs.dragino.com/docs/NB-IoT/general-configuration/cb-cs-models-nb-iot-lte-m/) | `{"Config":"[AT+SERVADDR=xxx.xxx.xxx.xxx,yyyy;ATZ]"}` — “changing the server address or protocol” |
| `AT+PRO=3,1` sets ThingSpeak server/payload (platform profile switch) | **High** | CB/CS ThingSpeak section | `AT+PRO=3,1 // Set to use ThingSpeak Server and Related Payload` |
| Live: re-applying `AT+PRO=3,3` / `3,5` without completed `ATZ` did **not** rewrite private SERVADDR to HiveMQ | **High** (this firmware/session) | `logs/20260807_095614_hivemq_trigger_tests.raw.log` | Device replies `Attention:Take effect after ATZ`; after `PRO=3,3` / `3,5` / back to `3,3`, `AT+SERVADDR=?` stayed `167.235.104.181,1883` |
| Live: `AT+TDC` change did not rewrite SERVADDR | **High** (this session) | Same log | `RESULT 1_TDC_1800: OK_PRIVATE` |
| Live: after setting private SERVADDR then `ATZ`, unit later resolved/connected using HiveMQ domain/IP | **Medium** (correlation; boot/password race may have blocked re-write) | `logs/20260806_121739_mqtt_ipfix.raw.log` | CFG showed `AT+SERVADDR=167.235.104.181,1883` → `ATZ` → later `Resolving domain name...` / `Domain IP:3.127.172.15,1883` → CFG `AT+SERVADDR=broker.hivemq.com,1883` |
| Docs prove factory default MQTT host **is** `broker.hivemq.com` for PS-CB-NA | **Not supported** | PS-CB-NA GE/1T text vs HiveMQ example | No primary quote equates factory SERVADDR with HiveMQ for this product |

---

## 3. Exact AT command sequences that rewrite (or can rewrite) server defaults

### 3.1 Direct hostname write (definitive HiveMQ string)

```text
AT+SERVADDR=broker.hivemq.com,1883
```

Documented as the space-stripping example; also the literal string observed on the live unit.

Related query:

```text
AT+SERVADDR=?
AT+CFG
```

### 3.2 Protocol / payload profile (server-related defaults — host not specified as HiveMQ)

```text
AT+PRO=3,3
```

Firmware response observed live: `Attention:Take effect after ATZ` then `OK`.

Documented sibling profiles that also change platform/payload behavior:

| Command | Documented intent |
|---|---|
| `AT+PRO=3,0` | MQTT + hex |
| `AT+PRO=3,1` | MQTT + ThingSpeak (“ThingSpeak Server and Related Payload”) |
| `AT+PRO=3,3` | MQTT + ThingsBoard (+ “other default server to ThingsBoard”) |
| `AT+PRO=3,5` | MQTT + JSON |
| `AT+PRO=2,x` / `4,x` | UDP / TCP profiles |

**Safe operational sequence if ThingsBoard payload is required on a private broker:**

```text
AT+PRO=3,3
ATZ
AT+SERVADDR=<PRIVATE_TB_HOST_OR_IP>,1883
AT+BKDNS=1,0,<PRIVATE_IP>,1883
AT+CFG
```

(Order: apply PRO → reboot if required → **re-assert SERVADDR/BKDNS** → verify.)

### 3.3 Factory reset (full parameter wipe)

```text
AT+FDR
```

or

```text
AT+FDR1
```

Restores factory parameters (FDR1 keeps passwords). **Exact factory `SERVADDR` for this SKU is not published as HiveMQ** in PS-CB-NA docs.

### 3.4 JSON / MQTT downlink rewrite (remote)

```text
{"Config":"[AT+SERVADDR=broker.hivemq.com,1883;ATZ]"}
```

or any `AT+SERVADDR=…` / `AT+PRO=…` inside the same Config bracket. Documented for CB stack downlink.

Risk amplifier on this unit’s historical CFG: `AT+SUBTOPIC=#` while connected to a public broker accepts broad subscriptions.

### 3.5 BKDNS (failover IP — not hostname rewrite)

```text
AT+BKDNS=1,0,<ip>,<port>
AT+BKDNS=2,<hours>,<ip>,<port>
AT+BKDNS=?
```

Default-style: `1,0,NULL`.

---

## 4. BKDNS failover mechanics

### 4.1 Documented behavior (NB/NS stack primary table; CB changelog + live CFG confirm presence)

From Dragino NB/NS §7.1.4 (`AT+BKDNS`):

| Parameter | Meaning |
|---|---|
| `a=1` | Disable dynamic domain update. IP saved after resolve; if next resolve fails, **last saved IP is used**. |
| `a=2` | Enable dynamic update on interval `b` (hours). Same last-IP fallback; periodic re-resolve. |
| `b` | Update interval in **hours** (when `a=2`). |
| `c` | Manual / cached IP in **same format as `AT+SERVADDR`**. If DNS fails, use `c`; if DNS succeeds, **`c` is updated** to the resolved IP. |

Default when queried empty: `1,0,NULL`.

Examples from the same page:

```text
AT+BKDNS=1,0
AT+BKDNS=2,1
AT+BKDNS=2,4,3.69.98.183,1883
```

### 4.2 How this produces a “HiveMQ jump” without changing the hostname

1. Device once used `AT+SERVADDR=broker.hivemq.com,1883`.
2. Resolve succeeds → `c` becomes e.g. `52.59.36.109,1883` or `3.127.172.15,1883` (observed live).
3. Operator sets `AT+SERVADDR=<private-host>,1883` but leaves HiveMQ in `BKDNS` `c`, **or** DNS to the private host fails.
4. Stack falls back to last saved IP → TCP/MQTT session to HiveMQ’s IP.
5. Uplink logs show `Domain IP:…` / “Successfully connected to the server” toward HiveMQ even though the operator believes the broker is private.

**Important distinction:** BKDNS explains **connecting to a HiveMQ IP**. It does **not** explain `AT+SERVADDR=?` returning the literal hostname `broker.hivemq.com` — that requires a write of SERVADDR (or factory/NVM reload of that string).

### 4.3 Related DNS toggles (CB/CS)

- `AT+GDNS=0` — default: resolve domain, use IP.
- `AT+GDNS=1` — do not resolve; communicate using domain string directly.

These interact with resolve/failover but are not documented as rewriting SERVADDR.

### 4.4 Live quirk

Setting `AT+BKDNS=1,0,167.235.104.181,1883` then immediately querying sometimes returned `1,0,NULL` until a later successful resolve/update cycle (`logs/20260807_095614_hivemq_trigger_tests.raw.log`, `logs/20260806_121739_mqtt_ipfix.raw.log`). Treat BKDNS readback as mandatory after any broker change.

---

## 5. How to detect + harden against the jump

### 5.1 Detection checklist (serial)

```text
<PIN>
AT+CFG
AT+SERVADDR=?
AT+BKDNS=?
AT+PRO=?
AT+SUBTOPIC=?
AT+GDNS=?
AT+TDC=?
```

**Fail if:**

- `SERVADDR` contains `hivemq` (or any unexpected public host).
- `BKDNS` `c` is a known HiveMQ IP while intending private ThingsBoard (observed examples: `3.127.172.15`, `18.198.118.51`, `52.59.36.109` on port `1883`).
- Uplink log shows `Resolving domain name...` / `Domain IP:` to a public HiveMQ address while private TB is expected.

### 5.2 Hardening

1. After **any** `AT+PRO=…` (especially `3,3` / `3,1`) and after **`ATZ`**, re-set and verify `SERVADDR` + `BKDNS`.
2. Prefer **IP form** for private TB if DNS is unreliable: `AT+SERVADDR=167.235.104.181,1883` and pin `AT+BKDNS=1,0,167.235.104.181,1883`.
3. Never leave HiveMQ in BKDNS `c`.
4. Avoid `AT+SUBTOPIC=#` on public or shared brokers; use a narrow topic (e.g. ThingsBoard attributes path).
5. Treat MQTT downlink Config as privileged; assume anything that can publish to the device’s SUBTOPIC can rewrite SERVADDR.
6. Do not run `AT+FDR` / `AT+FDR1` unless a full wipe is intended.
7. Do not paste Dragino changelog examples (`broker.hivemq.com`) into production scripts without substitution.

### 5.3 ThingsBoard private (token) pattern after profile changes

```text
AT+PRO=3,3
ATZ
AT+SERVADDR=<PRIVATE_HOST_OR_IP>,1883
AT+UNAME=<DEVICE_ACCESS_TOKEN>
AT+PWD=NULL
AT+PUBTOPIC=v1/devices/me/telemetry
AT+SUBTOPIC=v1/devices/me/attributes
AT+BKDNS=1,0,<PRIVATE_IP>,1883
AT+CFG
```

Official Dragino ThingsBoard.Cloud MQTT examples often use **device-name** topics/`UNAME`/`PWD` instead of token + `v1/devices/me/telemetry`. Either pattern still requires SERVADDR to be the intended broker, not HiveMQ.

---

## 6. Live serial tests (completed 2026-08-07) + remaining gaps

**Sendable debug report:** [`DEBUG_REPORT_HIVEMQ_JUMP.md`](DEBUG_REPORT_HIVEMQ_JUMP.md)

### 6.1 Completed on this unit (v1.1.4 / D-BG95-003)

| Test | Result | Log |
|---|---|---|
| Pre-state | HiveMQ `broker.hivemq.com` / `52.59.36.109`, `TDC=1800`, `PRO=3,3` | `20260807_095313_cfg_check2.raw.log` |
| Restore private SERVADDR | OK → `167.235.104.181,1883` | round1 |
| `AT+TDC=1800` | **No** hostname jump | round1 |
| `AT+PRO=3,3` / `3,5` / back `3,3` (no ATZ) | **No** jump | round1 |
| `AT+CFG` | **No** jump | round1 |
| `ATZ` with private already set | **Persists** private | round1 |
| GPS toggle | **No** jump | round1 |
| `AT+PRO=2,0` / `4,0` / `3,1` then back `3,3` (no ATZ) | **No** jump | round2 |
| Explicit `AT+SERVADDR=broker.hivemq.com,1883` | **JUMPS** (control) | round2 |
| Restore private + `AT+BKDNS=1,0,167…` + `ATZ` | **Persists** private + BKDNS IP | round2 |

Round1: `logs/20260807_095614_hivemq_trigger_tests.raw.log`  
Round2: `logs/20260807_100050_hivemq_trigger_round2.raw.log`

### 6.2 Still open (do not run destructive tests unless accepted)

| # | Test | Why it matters |
|---|---|---|
| A | `AT+PRO=3,3` → unlock → **`ATZ`** → CFG **without** re-writing SERVADDR | Isolates Type=3 + reboot default rewrite (round2 ATZ always followed a SERVADDR restore). |
| B | `AT+FDR1` after CFG backup → record factory SERVADDR | Whether factory host is HiveMQ / empty / ThingsEye for **this** SKU. |
| C | Private **hostname** SERVADDR + stale HiveMQ in BKDNS + DNS fail | Confirms IP failover without hostname rewrite. |
| D | Lab: HiveMQ + `SUBTOPIC=#` watch for Config downlinks | Remote rewrite hypothesis. |

---

## 7. Citations (full URLs / paths)

### Official Dragino documentation

1. PS-CB-NA user manual (payload Type=3, AT list, GE/1T):  
   https://docs.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/
2. PS-CB / PS-CS user manual (same Type=3 wording, AT list):  
   https://docs.dragino.com/docs/NB-IoT/flow-pressure-weight-sensors/ps-cb/
3. General configuration — CB & CS models (ThingsBoard MQTT, ThingSpeak `AT+PRO=3,1`, SERVADDR HiveMQ example §8.1.2, JSON downlink §8.2.7, BKDNS mention §8.3.5, GDNS):  
   https://docs.dragino.com/docs/NB-IoT/general-configuration/cb-cs-models-nb-iot-lte-m/
4. General configuration — NB & NS models (ThingsBoard MQTT, SERVADDR HiveMQ example §7.1.2, full `AT+BKDNS` table §7.1.4, FDR):  
   https://docs.dragino.com/docs/NB-IoT/general-configuration/nb-ns-models-nb-iot/
5. General configure manual mirror (CB/CS):  
   https://docs.dragino.com/docs/NB-IoT/general-configuration-manual/general-configure-for-cb-cs-models-nb-iot-lte-m/
6. General configure manual mirror (NB/NS):  
   https://docs.dragino.com/docs/NB-IoT/general-configuration-manual/general-configure-for-nb-ns-models-nb-iot/
7. Related Type=3 wording on PS-NB-NA:  
   https://docs.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-nb-na/

### Workspace primary extracts

8. `C:\Users\Arnor\Downloads\dragino\PPS-CB-NA – NB-IoT_LTE-M Analog Sensor.md` — companion export of product manual (Type=3 quote, AT+FDR).  
9. `C:\Users\Arnor\Downloads\dragino\PPS-CB-NA_NB-IoT_LTE-M_Analog_Sensor.md` — structured extract (secondary to official pages / companion MD).

### Live serial evidence (this device)

10. `C:\Users\Arnor\Downloads\dragino\logs\20260807_095614_hivemq_trigger_tests.raw.log` — Round1 PRO/TDC/ATZ/GPS trigger tests.  
11. `C:\Users\Arnor\Downloads\dragino\logs\20260807_100050_hivemq_trigger_round2.raw.log` — Round2 profile switches, explicit HiveMQ control jump, restore+ATZ persist.  
12. `C:\Users\Arnor\Downloads\dragino\logs\20260806_121739_mqtt_ipfix.raw.log` — private SERVADDR then ATZ then HiveMQ domain resolve.  
13. `C:\Users\Arnor\Downloads\dragino\logs\20260807_095313_cfg_check2.raw.log` — discovery CFG HiveMQ / `52.59.36.109`.  
14. `C:\Users\Arnor\Downloads\dragino\DEBUG_REPORT_HIVEMQ_JUMP.md` — consolidated debug report for sharing.  
15. Earlier HiveMQ CFG snapshots: `logs\20260806_114235_hivemq_probe.raw.log`, `logs\20260806_112527_ps_serial.raw.log`, `logs\20260805_161246_gps.raw.log`.

### Lead notes (not primary; cross-check only)

14. `C:\Users\Arnor\Downloads\dragino\HIVEMQ_BROKER_SWITCH.md`  
15. `C:\Users\Arnor\Downloads\dragino\AT_COMMANDS_HANDOFF.md`

---

## Appendix — Critical primary quotes

**Type=3 server side effect (PS-CB-NA / PS-CB):**

> Type3 payload special design for ThingsBoard, it will also configure other default server to ThingsBoard.

**HiveMQ SERVADDR example (CB/CS §8.1.2 / NB/NS §7.1.2):**

> Send command: `AT+SERVADDR=broker.hivemq.com,1883`  
> …  
> as follows:  
> `AT+SERVADDR=broker.hivemq.com,1883`

**BKDNS failover (NB/NS §7.1.4):**

> 1: Disable dynamic domain name update. The ip address will be saved after the domain name is resolved, if the next domain name resolution fails, the last saved ip address will be used.

**Factory reset:**

> `AT+FDR` // Reset Parameters to Factory Default.  
> `AT+FDR1` // Reset parameters to factory default values except for passwords.

**GE / 1T defaults (PS-CB-NA §2.1):**

> GE Version: This version doesn't include SIM card or point to any IoT server.  
> 1T Version: This version has 1NCE SIM card pre-installed and configure to send value to ThingsEye.
