# PS-CB-NA — Railway MQTT setup playbook (LLM / operator)

**Audience:** LLM agents or humans configuring this unit from a cold start (no prior chat context).  
**Device:** Dragino **PS-CB-NA** (analog mA/V → `idc_input` / `vdc_input`)  
**Validated firmware:** **PS-CB_v1.2.0** (`firmware/PS-CB_v1.2.0.hex`) · stack **D-BG95-003**  
**Target broker:** Railway Mosquitto — see [../docs/RAILWAY_MQTT.md](../docs/RAILWAY_MQTT.md)  
**Do not** paste real passwords into logs or commits. Load secrets from env files only.

Related deep-dives (do not duplicate here):

| Doc | When to open |
|---|---|
| [../docs/HIVEMQ_BROKER_SWITCH.md](../docs/HIVEMQ_BROKER_SWITCH.md) | `SERVADDR` jumped to `broker.hivemq.com` |
| [../archive/research/DEBUG_REPORT_FAILED_TO_SEND.md](../archive/research/DEBUG_REPORT_FAILED_TO_SEND.md) | Serial shows `Failed to send` after upload |
| [../docs/AT_COMMANDS_HANDOFF.md](../docs/AT_COMMANDS_HANDOFF.md) | Older ThingsBoard (`PRO=3,3`) AT shapes |
| [../docs/RAILWAY_MQTT.md](../docs/RAILWAY_MQTT.md) | Broker proxy host/port, smoke tests |

---

## 1. Purpose / when to use

Use this playbook to:

1. Point **PS-CB-NA** at **Railway MQTT** with a payload profile that **keeps a custom `SERVADDR` across reboot**.
2. Verify config with `AT+CFG` / query commands and an optional `ATZ` persistence check.
3. Confirm a few uplink cycles on **serial** and **MQTT** before declaring success.

**Prefer `AT+PRO=3,5` (JSON MQTT)** for this workspace.  
Alternative per Dragino support: `AT+PRO=3,0` (HEX) also retains custom `SERVADDR`.  
**Do not use `AT+PRO=3,3`** (ThingsBoard type) when targeting a custom broker — on restart it resets `SERVADDR` to `broker.hivemq.com`.

Automated helpers (from repo root):

```powershell
# Prefer one fused session (serial + MQTT) instead of two agents
python shared/session_monitor.py --device ps-cb --policy stable --cycles 3

python PS-CB-NA/scripts/configure_pscb_railway.py
python PS-CB-NA/scripts/configure_pscb_tb_pro35.py   # includes ATZ persistence check
python PS-CB-NA/scripts/observe_uplink_cycles.py --port COM8   # observe-only
```

**Unlock:** `shared/monitor.py --unlock-now` sends the PIN but marks unlocked **only** after `Password Correct`.  
**Failed to send:** if `Upload data successfully` already happened this cycle, treat as teardown false-positive (`fp` in status), not radio fail.

---

## 2. Hardware & serial

| Item | Value / note |
|---|---|
| UART | USB-TTL: GND↔GND, TX↔RX, RX↔TX |
| Baud | **9600 8N1** — trailing newline on every AT line |
| Port (typical) | **COM8** — **may vary**; confirm before opening |
| SW1 | **Flash** for console / normal run · **ISP** only when flashing |
| Unlock PIN | Workspace `.env` → `DRAGINO_PIN` (gift-box sticker). Never commit the PIN. |
| Wake | Device sleeps between uplinks; press **ACT 1–3 s** (force uplink) or wait for TX; `AT+DEBUG=1` increases log noise |

List ports:

```powershell
python shared/monitor.py --list-ports
```

Open monitor / unlock (PIN from `.env`):

```powershell
python shared/monitor.py --port COM8 --baud 9600 --unlock-now --debug
# or fused serial+MQTT:
python shared/session_monitor.py --device ps-cb --policy stable --cycles 3
```

Short test interval (then restore longer for production):

```powershell
python shared/monitor.py --port COM8 --unlock-now --debug --poll 60 --set-cycle 120
```

**Env files (secrets — reference only):**

| File | Keys |
|---|---|
| `.env` | `DRAGINO_PIN`, optional `DRAGINO_PORT` / `DRAGINO_BAUD` |
| `railway-mqtt.local.env` | `MQTT_USER`, `MQTT_PASS`, host/port (see `railway-mqtt.env.example`) |

Copy template if needed: `railway-mqtt.env.example` → `railway-mqtt.local.env` (gitignored).

---

## 3. Critical gotchas

| Gotcha | Rule |
|---|---|
| **`AT+PRO=3,3`** | ThingsBoard payload — **resets `SERVADDR` to `broker.hivemq.com` on reboot** (Dragino support + live evidence). Use **`3,5` or `3,0`** for custom brokers. |
| **Order** | Unlock → **PRO** → **SERVADDR** → auth/topics → **BKDNS** → TDC → CFG verify → optional **ATZ** → re-check SERVADDR |
| **`AT+BKDNS` sticky IP** | Old HiveMQ IPs can remain as failover. Pin Railway IP or clear after broker change. |
| **`Failed to send` after `Upload data successfully`** | Often **TCP teardown false positive** — treat **Upload success** (+ MQTT message) as ground truth. |
| **`Failed to send` + Signal/CSQ 99, no Upload success** | **Real radio / attach fail** — wait for attach, check SIM/APN; **`ATZ`** can recover radio. |
| **`AT+FDR` / `AT+FDR1`** | Factory wipe → demo defaults again. **Do not use** unless intentional. |
| **COM exclusive** | Only one process may hold COM8; close monitors before flash or parallel scripts. |
| **TDC** | `AT+TDC=120` is fine for testing; restore a longer interval for production (e.g. 180 / 1800). |

---

## 4. Target config (Railway MQTT)

| Setting | Value |
|---|---|
| Hostname | `altaria.proxy.rlwy.net` |
| Fallback IP | `66.33.22.220` |
| Port | **33239** (Railway TCP proxy — **not** container `1883`) |
| Username | from `railway-mqtt.local.env` → `MQTT_USER` (example template: `dragino`) |
| Password | from `railway-mqtt.local.env` → `MQTT_PASS` → use `<MQTT_PASS>` below |
| Client ID | `ps-cb` |
| Publish | `dragino/ps-cb/up` |
| Subscribe | `dragino/ps-cb/down` |
| TLS | Off → `AT+TLSMOD=0,0` |
| QoS | `1` |
| Profile | **`AT+PRO=3,5`** (JSON) |
| TDC (test) | `120` seconds |
| Firmware | `PS-CB_v1.2.0` |

Prefer **IP** for `SERVADDR` / `BKDNS` on this carrier path (`66.33.22.220,33239`). Hostname form also works if DNS is reliable:

```text
AT+SERVADDR=altaria.proxy.rlwy.net,33239
```

---

## 5. Exact AT sequence (copy-paste)

Every line needs a trailing newline. Unlock first with the real PIN from `.env` (never hard-code in docs/commits):

```text
<DRAGINO_PIN>
```

or:

```text
AT+PIN=<DRAGINO_PIN>
```

Expect `Password Correct` (or equivalent) before continuing.

### Apply Railway JSON MQTT

```text
AT+PRO=3,5
AT+TLSMOD=0,0
AT+SERVADDR=66.33.22.220,33239
AT+UNAME=<MQTT_USER>
AT+PWD=<MQTT_PASS>
AT+PUBTOPIC=dragino/ps-cb/up
AT+SUBTOPIC=dragino/ps-cb/down
AT+CLIENT=ps-cb
AT+MQOS=1
AT+BKDNS=1,0,66.33.22.220,33239
AT+TDC=120
```

**Re-assert after PRO** (belt-and-suspenders — PRO can rewrite defaults):

```text
AT+SERVADDR=66.33.22.220,33239
AT+BKDNS=1,0,66.33.22.220,33239
AT+CLIENT=ps-cb
AT+UNAME=<MQTT_USER>
AT+PWD=<MQTT_PASS>
AT+PUBTOPIC=dragino/ps-cb/up
AT+SUBTOPIC=dragino/ps-cb/down
```

### Alternative: HEX payload (also keeps custom SERVADDR)

```text
AT+PRO=3,0
```

Then the same `SERVADDR` / auth / topics / `BKDNS` / `TDC` block as above.

### Optional APN (only if SIM requires it)

```text
AT+APN=?
AT+APN=<APN_FOR_THIS_SIM>
```

Example seen on this workspace SIM family: `lpwa.vodafone.is` — **confirm for the SIM in the device**; do not invent.

---

## 6. Verify

Still unlocked:

```text
AT+CFG
AT+SERVADDR=?
AT+BKDNS=?
AT+PRO=?
AT+CLIENT=?
AT+UNAME=?
AT+PUBTOPIC=?
AT+SUBTOPIC=?
AT+TLSMOD=?
AT+TDC=?
```

### Pass criteria (pre-reboot)

| Check | Must be |
|---|---|
| `PRO` | `3,5` (or `3,0`) — **not** `3,3` |
| `SERVADDR` | `66.33.22.220,33239` or `altaria.proxy.rlwy.net,33239` — **never** `broker.hivemq.com` |
| `BKDNS` | Same Railway host/IP:port — **no** HiveMQ public IPs (`3.127…`, `18.198…`, etc.) |
| `CLIENT` | `ps-cb` |
| `UNAME` | matches `MQTT_USER` |
| `PUBTOPIC` / `SUBTOPIC` | `dragino/ps-cb/up` / `dragino/ps-cb/down` |
| `TLSMOD` | `0,0` |
| `TDC` | `120` for test (or your production value) |

### ATZ persistence check (critical after PRO changes)

```text
ATZ
```

Wait for boot (~20–40 s), unlock again, then:

```text
AT+SERVADDR=?
AT+PRO=?
AT+BKDNS=?
```

**Pass:** Railway address still present and `PRO` still `3,5` or `3,0`.  
**Fail:** `broker.hivemq.com` or wrong port → you are still on `3,3` or PRO did not stick; re-apply section 5 (never leave HiveMQ).

---

## 7. Monitor a few cycles

### Serial (success pattern)

Good cycle:

```text
*****Upload start:N*****
… signal strength / attach …
Opened the MQTT client network successfully
Successfully connected to the server
Upload data successfully
Subscribe to topic successfully
  (~tens of seconds later — optional noise)
Failed to close TCP connection    ← often harmless teardown
Failed to send                    ← false positive IF Upload success already seen
*****End of upload*****
```

| Serial pattern | Meaning |
|---|---|
| `Upload data successfully` (+ MQTT msg) | **Success** — ignore later `Failed to send` |
| Signal / CSQ **99**, no Upload success | **Real fail** — radio not ready; wait or `ATZ` |
| Connect to HiveMQ / wrong Domain IP | Config wrong — fix SERVADDR/BKDNS/PRO |

Observe-only script:

```powershell
python PS-CB-NA/scripts/observe_uplink_cycles.py
```

Or live UART:

```powershell
python shared/monitor.py --port COM8 --unlock-now --debug --poll 60
```

### MQTT (broker side)

```powershell
python shared/mqtt_listen_railway.py
# or smoke:
python shared/mqtt_smoke_railway.py
```

Expect JSON (or HEX if `PRO=3,0`) on **`dragino/ps-cb/up`**. Validated session example shape: `idc_input`, battery, `signal` ~20–31 (not 99).

With `TDC=120`, watch **≥2–3 cycles** (~4–10 min) before calling config done. Then restore a longer TDC for production if desired:

```text
AT+TDC=180
```

(or `1800` / project policy).

---

## 8. Firmware update

**When:** Dragino recommends upgrade if `Failed to send` persists after switching off `PRO=3,3`; this workspace target is **v1.2.0**.

| Item | Path / note |
|---|---|
| Image | `PS-CB-NA/firmware/PS-CB_v1.2.0.hex` |
| Mode | SW1 = **ISP** (no console; LED/firmware idle until flash done) |
| Tool | **STM32CubeProgrammer** (GUI or CLI) |
| Address | `.hex` carries addresses — let the programmer auto-place (do not invent offsets) |
| After flash | SW1 back to **Flash**, power-cycle / RESET, unlock, re-apply section 5 |

Official steps (do not duplicate the full guide here):

- [UART TTL upgrade for NB-IoT/LTE-M](https://wiki.dragino.com/docs/NB-IoT/firmware-update/uart-ttl-upgrade-for-nb-iot-lte-m-devices/)
- BLE OTA alternative: [BLE upgrade](https://wiki.dragino.com/docs/NB-IoT/firmware-update/ble-upgrade-for-nb-iot-lte-m-end-node/)
- Product page: [PS-CB-NA wiki](https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/)

Close any process holding the COM port before CubeProgrammer connects. After upgrade, confirm version via boot banner / `AT+CFG` / Image Version string (`v1.2.0`).

---

## 9. LLM checklist

### Do

- [ ] Confirm COM port (`--list-ports`); default assumption COM8 only if listed
- [ ] Load `DRAGINO_PIN` and `MQTT_PASS` from env — use placeholders in any written notes
- [ ] Set **`AT+PRO=3,5`** (or `3,0`), then **SERVADDR** to Railway **:33239**
- [ ] Set matching **BKDNS** (no HiveMQ leftovers)
- [ ] Verify with `AT+CFG` / `SERVADDR=?` / `PRO=?`
- [ ] Run **`ATZ`** and re-check SERVADDR survives
- [ ] Watch ≥2 uplink cycles: serial **Upload success** + MQTT on `dragino/ps-cb/up`
- [ ] Treat post-success `Failed to send` as teardown noise unless Upload never appeared
- [ ] Restore longer TDC before leaving the device in production

### Don't

- [ ] **Don't** use `AT+PRO=3,3` for Railway / custom brokers
- [ ] **Don't** assume SERVADDR sticks without an ATZ check after PRO changes
- [ ] **Don't** leave `broker.hivemq.com` or HiveMQ IPs in SERVADDR/BKDNS
- [ ] **Don't** run `AT+FDR` / `AT+FDR1` casually
- [ ] **Don't** paste real PIN / MQTT password into README, commits, or shared logs
- [ ] **Don't** declare radio healthy on Signal/CSQ **99** with no Upload success
- [ ] **Don't** hold COM open in two tools at once
- [ ] **Don't** invent credentials — if env missing, stop and ask

---

## Quick reference — healthy end state

```text
AT+PRO=3,5
AT+SERVADDR=66.33.22.220,33239
AT+BKDNS=1,0,66.33.22.220,33239
AT+CLIENT=ps-cb
AT+UNAME=<MQTT_USER>
AT+PUBTOPIC=dragino/ps-cb/up
AT+SUBTOPIC=dragino/ps-cb/down
AT+TLSMOD=0,0
AT+TDC=120          # test; raise for production
```

After `ATZ`, the same SERVADDR/PRO must still read back. Uplink: `Upload data successfully` on serial and a message on Railway topic `dragino/ps-cb/up`.
