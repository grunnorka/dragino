# Debug Report: PS-CB-NA jumps to HiveMQ broker

**Date:** 2026-08-07 (updated after Reset / `ATZ` persist tests)  
**Device:** Dragino PS-CB-NA · FW **v1.1.4** · Stack **D-BG95-003** · IMEI `869181074157262`  
**Serial:** COM8 @ 9600 8N1  
**Private target:** ThingsBoard MQTT `167.235.104.181:1883` (token + `v1/devices/me/telemetry`)  
**Full research:** [`RESEARCH_HIVEMQ_JUMP_TRIGGERS.md`](RESEARCH_HIVEMQ_JUMP_TRIGGERS.md)  
**Session handoff:** `%TEMP%\dragino-ps-cb-na-handoff.md`

---

## Summary

The sensor stopped sending usable data to private ThingsBoard because MQTT `SERVADDR` kept showing **`broker.hivemq.com`**. Soft AT changes do not cause that while the MCU stays up. **Any MCU reboot** — hardware **Reset** button or **`ATZ`** — rewrites the `SERVADDR` **hostname** to HiveMQ on this firmware, even though it is **not** a factory parameter wipe (`TDC`, topics, `BKDNS`, `PRO` survive). After reboot the modem DNS-resolves HiveMQ and publishes there (TB never sees the data). Workaround: after every reboot, unlock and re-set `AT+SERVADDR` / `AT+BKDNS` to the private IP; do **not** expect `ATZ` to “save” the broker.

---

## 1. Verdict

| Question | Answer |
|---|---|
| Is the Reset button a full parameter wipe? | **No.** Manual: “Press to reboot the device.” |
| Does `ATZ` wipe all parameters? | **No.** Live marker `AT+TDC=181` **survived** `ATZ`. |
| What *does* factory-wipe? | `AT+FDR` / `AT+FDR1` only. |
| What puts HiveMQ back? | **MCU reboot** (Reset or `ATZ`) rewrites `SERVADDR` hostname to `broker.hivemq.com`. Also: explicit `AT+SERVADDR=broker.hivemq.com,1883`. |
| Soft `PRO`/`TDC`/GPS/`CFG` without reboot? | **Do not** rewrite hostname (tested). |

### Why no ThingsBoard data?

Not “too few cycles.” `TDC=180` (3 min) is fine. While `SERVADDR` is HiveMQ, telemetry (ThingsBoard token + `v1/devices/me/telemetry`) is published to the **public** broker → private TB stays empty.

---

## 2. Symptom when found (2026-08-07 ~09:53 UTC)

| Field | Value |
|---|---|
| `AT+SERVADDR` | `broker.hivemq.com,1883` → `52.59.36.109` |
| `AT+BKDNS` | `1,0,52.59.36.109,1883` |
| `AT+PRO` | `3,3` |
| Auth / pub topic | ThingsBoard token + `v1/devices/me/telemetry` (endpoint mismatch) |
| `AT+TDC` | `1800` at discovery (later restored to `180`) |
| Uplink | Resolving/dialing HiveMQ |

Log: `logs/20260807_095313_cfg_check2.raw.log`

---

## 3. Docs: Reset vs ATZ vs FDR

| Action | Manual meaning | Effect on params (live) |
|---|---|---|
| **Reset button** (§1.8.3) | “Press to reboot the device.” | Reboot; **not** FDR. Hostname → HiveMQ; `TDC`/topics/`PRO` kept. |
| **`ATZ`** | “Trig a reset of the MCU” | Same class as Reset. `TDC=181` kept; **SERVADDR hostname → HiveMQ**. |
| **`AT+FDR` / `AT+FDR1`** | Factory default parameters | Full wipe (not executed this session). |

Sources: [PS-CB-NA](https://docs.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/) · workspace `PPS-CB-NA – NB-IoT_LTE-M Analog Sensor.md`

Related doc notes (research file): Type=3 “configure other default server **to ThingsBoard**”; `broker.hivemq.com` is Dragino’s **SERVADDR example**; `AT+BKDNS` sticky last-resolved IP; JSON downlink can set `SERVADDR`.

---

## 4. Live trigger matrix (2026-08-07)

### Soft triggers (no reboot) — Round 1 / 2

Logs: `logs/20260807_095614_hivemq_trigger_tests.*`, `logs/20260807_100050_hivemq_trigger_round2.raw.log`

| Test | Result |
|---|---|
| `AT+TDC`, GPS toggle, `AT+CFG` | No HiveMQ hostname jump |
| `AT+PRO=3,3` / `3,5` / `3,1` / UDP / TCP (no completed ATZ) | No jump |
| Explicit `AT+SERVADDR=broker.hivemq.com,1883` | **JUMPS** (control) |

### Hardware Reset button

Log: `logs/20260807_101812_restore_then_hw_reset.raw.log`

| Phase | Result |
|---|---|
| Pre | `SERVADDR=167.235.104.181,1883` |
| Button | `DRAGINO NB bootloader v1.3` |
| Post | **`SERVADDR=broker.hivemq.com,1883`**, `Domain IP:18.158.225.193` |
| Kept | `PRO=3,3`, TB topics, `TDC=180` |

### ATZ persist tests (definitive)

| Log | Setup | After reboot |
|---|---|---|
| `logs/20260807_103006_atz_persist_test.raw.log` | Private SERVADDR + **`TDC=181`** + `PRO=3,3` then `ATZ` | **`TDC=181` kept**; topics/`BKDNS` kept; **`SERVADDR=broker.hivemq.com`** (paren IP still showed `167.235…`) |
| `logs/20260807_103247_atz_no_pro_persist.raw.log` | Private SERVADDR/`BKDNS` **without** re-writing `PRO`, then `ATZ` | Again **`SERVADDR=broker.hivemq.com`**; `BKDNS`/`TDC` kept; later uplink **`Domain IP:18.156.19.212`** (HiveMQ DNS, not private IP) |

**Conclusion:** Reboot is normal and expected for Reset/`ATZ`. It is **not** “reset all parameters.” On this FW, reboot **does** force the MQTT hostname string back to HiveMQ, then the stack resolves/dials HiveMQ even if `BKDNS` still holds the private IP.

---

## 5. Historical regression (Aug 6)

Log: `logs/20260806_121739_mqtt_ipfix.raw.log`

Same pattern: set private SERVADDR → `ATZ` → later `Resolving domain name...` / HiveMQ `Domain IP` / CFG `SERVADDR=broker.hivemq.com`. Confounded at the time by password-locked writes and `SUBTOPIC=#`; today’s controlled `ATZ` tests show the hostname rewrite is **reproducible on reboot alone**.

---

## 6. Current device state (end of session)

After last restore **without** `ATZ` (restore ~10:38 UTC):

- Target: `AT+SERVADDR=167.235.104.181,1883` + matching `AT+BKDNS`
- `AT+PRO=3,3`, `AT+TDC=180`, TB token topics
- **Caveat:** next Reset/`ATZ` will likely put HiveMQ hostname back until SERVADDR is re-applied

Earlier in-session after a good private restore: serial showed **Upload data successfully** (~10:28 UTC) — check ThingsBoard for that window.

---

## 7. Hardening checklist (operational)

1. **After every Reset button or `ATZ`:** unlock → set `AT+SERVADDR=<private-ip>,1883` → `AT+BKDNS=1,0,<private-ip>,1883` → verify with `=?` / uplink `Domain IP`.
2. If `BKDNS`/`PRO` require ATZ (“Take effect after ATZ”), run ATZ then **immediately re-set SERVADDR again** (ATZ alone will not keep the private hostname).
3. Prefer **IP** form for private TB (avoid DNS to a hostname the FW may replace).
4. Never leave HiveMQ in `BKDNS` `c`; never use `SUBTOPIC=#` on public brokers.
5. Do not paste `broker.hivemq.com` from Dragino examples into production scripts.
6. Do not use `AT+FDR`/`AT+FDR1` unless a full wipe is intended.
7. Trust uplink **`Domain IP:`** lines over a comforting parenthetical IP next to a HiveMQ hostname.

---

## 8. Still open

| # | Item | Notes |
|---|---|---|
| A | Can SERVADDR be made reboot-sticky on v1.1.4? | No durable recipe found yet; treat post-reboot re-set as required. |
| B | Is rewrite tied only to `PRO=3,3` profile defaults? | Suspected (Type=3 “default server”); not proven against other `PRO` values across reboot. |
| C | `AT+FDR1` factory SERVADDR dump | Destructive; CFG backup first. |
| D | MQTT Config downlink while on HiveMQ | Documented capability; not required to explain reboot rewrite. |

---

## 9. Evidence index

| File | Role |
|---|---|
| `RESEARCH_HIVEMQ_JUMP_TRIGGERS.md` | Primary-source research |
| `logs/20260807_095313_cfg_check2.raw.log` | Discovery CFG (HiveMQ) |
| `logs/20260807_095614_hivemq_trigger_tests.raw.log` | Soft triggers round 1 |
| `logs/20260807_100050_hivemq_trigger_round2.raw.log` | Soft triggers + explicit HiveMQ control |
| `logs/20260807_101812_restore_then_hw_reset.raw.log` | Hardware Reset → HiveMQ |
| `logs/20260807_103006_atz_persist_test.raw.log` | ATZ keeps `TDC=181`, rewrites SERVADDR |
| `logs/20260807_103247_atz_no_pro_persist.raw.log` | ATZ without PRO write; still HiveMQ hostname + HiveMQ Domain IP |
| `logs/20260806_121739_mqtt_ipfix.raw.log` | Aug 6 ATZ → HiveMQ episode |
| `HIVEMQ_BROKER_SWITCH.md` | Earlier working notes |
| `AT_COMMANDS_HANDOFF.md` | Command shapes for serial ops |
