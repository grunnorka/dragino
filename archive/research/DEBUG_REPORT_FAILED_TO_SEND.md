# Debug Report: PS-CB-NA logs `Failed to send` on private ThingsBoard

**Date:** 2026-08-07  
**Device:** Dragino PS-CB-NA · FW **v1.1.4** · Stack **D-BG95-003** · IMEI `869181074157262`  
**Serial:** COM8 @ 9600 8N1  
**Broker under test:** Private ThingsBoard MQTT `167.235.104.181:1883`  
**Profile:** `AT+PRO=3,3` · TLS off (`AT+TLSMOD=0,0`) · token auth · `v1/devices/me/telemetry` / `v1/devices/me/attributes`  
**TDC:** 180 s · `AT+CDP=0` cleared before capture  

Related (separate issue): [`DEBUG_REPORT_HIVEMQ_JUMP.md`](DEBUG_REPORT_HIVEMQ_JUMP.md) — reboot rewriting `SERVADDR` to `broker.hivemq.com`.

---

## Summary

On the **private ThingsBoard broker**, every controlled uplink cycle shows a successful MQTT publish and subscribe, then ~65 seconds later logs **`Failed to close TCP connection`** → **`Failed to send`** → **`*****End of upload*****`**.  
`Failed to send` is therefore **not** “MQTT telemetry failed.” It is a reproducible **post-success teardown / secondary path** message while `SERVADDR` remains the private IP.

---

## 1. Verdict

| Question | Answer |
|---|---|
| Does the private uplink fail? | **No** for the MQTT publish path — both cycles showed `Upload data successfully` and `Subscribe to topic successfully`. |
| What does `Failed to send` mean here? | A later step after MQTT success: firmware reports TCP close failure, then `Failed to send`, then ends the upload window. |
| Is it intermittent? | **No** in this test — **2/2** identical cycles. |
| Is the broker wrong (HiveMQ public)? | **No** for this capture — `AT+SERVADDR=167.235.104.181,1883` and matching `AT+BKDNS` before listen. |
| Operational impact | Serial looks alarming every cycle; ThingsBoard can still receive the successful publish. Treat success line as ground truth, not `Failed to send`. |

---

## 2. Controlled config (pre-listen)

```text
AT+SERVADDR=167.235.104.181,1883
AT+BKDNS=1,0,167.235.104.181,1883
AT+PRO=3,3
AT+TDC=180
AT+PUBTOPIC=v1/devices/me/telemetry
AT+SUBTOPIC=v1/devices/me/attributes
AT+CDP=0
AT+TLSMOD=0,0   (confirmed on restore; TLS not used for private TB)
```

No `ATZ` during this capture (avoids the separate HiveMQ hostname rewrite bug).

---

## 3. Observed sequence (both cycles)

```text
*****Upload start:N*****
… sensor values …
Opened the MQTT client network successfully
Successfully connected to the server
Upload data successfully
Subscribe to topic successfully
  (~65 s later)
Failed to close TCP connection
Failed to send
*****End of upload*****
Closing NB module...
```

### Cycle matrix

| Cycle | MQTT connect | Upload success | Subscribe OK | `Failed to close TCP` | `Failed to send` | End of upload |
|---|---|---|---|---|---|---|
| 1 (`Upload start:1`) | Yes | Yes | Yes | Yes | Yes | Yes |
| 2 (`Upload start:2`) | Yes | Yes | Yes | Yes | Yes | Yes |

Approximate timing (cycle 1): publish ~11:36:38 UTC · `Failed to send` ~11:37:47 UTC (~69 s later).

---

## 4. Interpretation

1. Primary path (`PRO=3,3` MQTT → ThingsBoard) **succeeds**.
2. Firmware still runs a **TCP close / secondary send** step that fails and prints `Failed to send`.
3. Upload window then ends; modem often powers down until the next TDC.
4. This matches earlier private-TB serial snippets (e.g. `logs/20260807_102445_restore_persist.raw.log`) where `Failed to send` appeared beside a later successful MQTT path — the fresh test isolates it as **always present after success** on private TB, not only when pointed at public HiveMQ.

**Do not confuse** with the HiveMQ-jump case: when `SERVADDR=broker.hivemq.com`, telemetry never reaches private TB even if some MQTT ACKs appear.

---

## 5. Hardening / operator notes

1. For private TB health, watch **`Upload data successfully`** and ThingsBoard ingestion — not the absence of `Failed to send`.
2. Keep `SERVADDR` / `BKDNS` on the private IP; after any Reset/`ATZ`, re-apply them (see HiveMQ jump report).
3. Prefer `SUBTOPIC=v1/devices/me/attributes` (not `#`).
4. Optional: clear cache with `AT+CDP=0` when diagnosing retries; this test still saw `Failed to send` after a clear.

---

## 6. Still open

| # | Item | Notes |
|---|---|---|
| A | Exact firmware step behind “Failed to close TCP” / second send | Not named in the PS-CB-NA manual excerpt used here; looks like teardown or cache flush after MQTT. |
| B | Whether a Dragino FW update removes the message | Not tested. |
| C | Whether QoS / `AT+MQOS` changes the teardown failure | Not tested this session. |

---

## 7. Evidence index

| File | Role |
|---|---|
| `logs/20260807_113436_failed_send_fresh.raw.log` | **Primary** — CDP clear, private SERVADDR, 2 full cycles |
| `logs/20260807_102445_restore_persist.raw.log` | Supporting — earlier private restore with same `Failed to send` + later MQTT success |
| `DEBUG_REPORT_HIVEMQ_JUMP.md` | Separate bug: reboot → `broker.hivemq.com` |
