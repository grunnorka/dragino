# PS-CB-NA — handoff for the next LLM / operator

**Date:** 2026-08-16  
**Device state:** firmware healthy, MQTT config correct and persisted, **no broker messages yet**.  
**Read these in order:** this file → [SETUP.md](SETUP.md) → [FIRMWARE_UPDATE.md](FIRMWARE_UPDATE.md) (only if flashing or dark board).

Do **not** re-flash or re-run factory reset unless something is broken. Config is already good.

---

## Where things stand

| Layer | Status |
|---|---|
| Hardware / UART | USB-TTL on `/dev/ttyUSB0`, SW1 = Flash, PIN from `.env` (`DRAGINO_PIN`) |
| Bootloader + app | Dragino NB bootloader **v1.3** + app **v1.2.1** (recovered after a mass-erase brick) |
| Radio / SIM | Vodafone GDSP (`IMSI 90128…`), `APN=NULL`, signal ~25–31, PDP + DNS OK |
| MQTT parameters | All 12 fields correct after `AT+FDR1` + re-apply; survive `ATZ` |
| Broker (from PC) | `altaria.proxy.rlwy.net:33239` accepts auth (`CONNECT 0`); broker region now **EU West** (was `sfo`; public host/IP/port unchanged) |
| Device → broker | **Open.** TCP sometimes opens; `Successfully connected to the server` never appears; 0 MQTT messages — re-test after EU move |

Evidence log: `logs/20260816_161105_pscb_reset_reapply.log`  
Proof that the port is **not** blocked:

```text
[300346]Opened the MQTT client network successfully   # TCP to 66.33.22.220:33239 OK
[304912]Failed to send                                # ~4.5 s later, no CONNACK
```

Earlier hypothesis ("SIM blocks high port 33239") is **ruled out**.

---

## Target config (already on the device)

| Key | Value |
|---|---|
| `PRO` | `3,5` (JSON MQTT — never `3,3`) |
| `SERVADDR` / `BKDNS` | `66.33.22.220,33239` / `1,0,66.33.22.220,33239` |
| `CLIENT` | `ps-cb` |
| `UNAME` / `PWD` | from `railway-mqtt.local.env` |
| `PUBTOPIC` / `SUBTOPIC` | `dragino/ps-cb/up` / `dragino/ps-cb/down` |
| `TLSMOD` / `MQOS` / `TDC` | `0,0` / `1` / `180` |
| `APN` | `NULL` (not empty string — use `AT+APN=NULL`) |

Secrets live in gitignored `.env` and `railway-mqtt.local.env`. Never paste them into docs or commits.

---

## What to try next (cheapest first)

1. **Watch Railway mosquitto logs** during an upload cycle (`TDC=180`).  
   - Inbound `CONNECT` seen → device times out before `CONNACK` (proxy latency).  
   - Nothing logged → bytes not reaching the broker (proxy / encoding).
2. **`AT+MQOS=0`** then `ATZ`, watch one cycle — removes `PUBACK` wait.
3. **`diag_pscb_ports.py`** phase B (public `54.36.178.49:1883`, anonymous, IP only) to see if *any* broker completes the handshake. Interpretation: handshake works elsewhere ≠ port block.
4. Only if config drifts: `fix_pscb_mqtt.py`. Full wipe+rewrite: `reset_and_apply_pscb.py`.

---

## Scripts to use (Linux)

From repo root, with `.venv` and `sudo chmod 666 /dev/ttyUSB0` if needed:

| Prefer | Purpose |
|---|---|
| `PS-CB-NA/scripts/reset_and_apply_pscb.py` | `AT+FDR1` + full Railway rewrite + persistence + broker watch |
| `PS-CB-NA/scripts/fix_pscb_mqtt.py` | Repair only wrong fields |
| `PS-CB-NA/scripts/verify_pscb.py` | Boot banner + unlock + `AT+CFG` dump |
| `PS-CB-NA/scripts/diag_pscb_ports.py` | A/B Railway vs public broker (IP only) |
| `PS-CB-NA/scripts/recover_pscb.py` | Bootloader + app flash (ISP) — **only if dark/silent** |
| `shared/session_monitor.py --device ps-cb` | Fused serial + MQTT watch |

Hardware steps use `shared/prompt_user.py` (zenity popup + notify-send). Chat text alone is easy to miss.

**Avoid for day-to-day work:** `flash_pscb.py` (app-only, deprecated), older `configure_pscb_*.py` (Windows COM-era; prefer the Linux scripts above).

---

## Hard rules (do not re-learn the hard way)

1. **Never mass-erase** flash. Bootloader at `0x08000000`, app hex at `0x08007800`. STM32L0 erased flash reads `0x00`, not `0xFF`.
2. Sandbox hides `/dev/ttyUSB*`. Serial / zenity need full host permissions.
3. Parse AT with `shared/at_session.py` — async `[12345]…` lines interleave with replies.
4. `AT+PRO` needs **`ATZ`**. Confirm via boot banner `Protocol in Used: MQTT`.
5. `AT+APN=` (empty) ≠ `AT+APN=NULL`. This SIM wants `NULL`.
6. `AT+FDR1` returns **no OK** (reboots immediately) and only resets `PRO`/`TDC`/`MQOS`/`BKDNS` — not `SERVADDR`/auth/topics.
7. Do not use `AT+PRO=3,3` for Railway.

Full detail: [FIRMWARE_UPDATE.md](FIRMWARE_UPDATE.md) · [SETUP.md](SETUP.md) · [../docs/RAILWAY_MQTT.md](../docs/RAILWAY_MQTT.md).
