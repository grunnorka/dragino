# HiveMQ broker jump and persistence (PS-CB-NA)

What rewrites the MQTT `SERVADDR` back to `broker.hivemq.com`, and how to keep a custom broker across reboots.

---

## v1.1.4 — reboot rewrites `SERVADDR` to HiveMQ

Firmware `v1.1.4` (stack `D-BG95-003`) observed on PS-CB-NA COM8, IMEI `869181074157262`.

| Action | Effect on `SERVADDR` hostname | Notes |
|---|---|---|
| `ATZ` (MCU reboot) | **Rewrites to `broker.hivemq.com`** | Not a full parameter wipe; `TDC`, topics, `BKDNS`, `PRO` survive |
| Reset button | **Rewrites to `broker.hivemq.com`** | Manual: “Press to reboot the device” |
| `AT+TDC` change | No rewrite | Tested live |
| `AT+PRO=3,3` / `3,5` / `3,1` (no `ATZ`) | No rewrite | Soft profile changes stay in RAM until reboot |
| `AT+CFG` | No rewrite | Just dumps settings |
| Explicit `AT+SERVADDR=broker.hivemq.com,1883` | **Jumps** | Direct write; the string is Dragino’s documented SERVADDR example |

After the hostname is rewritten, the modem resolves `broker.hivemq.com` and publishes there. ThingsBoard credentials/topics remain in place, so the data never reaches the private broker.

**Root cause on v1.1.4:** `AT+PRO=3,3` (ThingsBoard payload type) is documented to “also configure other default server to ThingsBoard,” but the firmware’s stored default MQTT host for that profile is Dragino’s example `broker.hivemq.com`. Reboot loads that default unless the profile is avoided.

---

## v1.2.1 — use `PRO=3,5` (or `3,0`) to keep a custom broker

Firmware `PS-CB-NA_v1.2.1` fixes the persistence issue for custom brokers.

| Profile | Custom `SERVADDR` survives `ATZ`? | Use case |
|---|---|---|
| `AT+PRO=3,5` (MQTT + JSON) | **Yes** | Private ThingsBoard, Railway, or any custom broker |
| `AT+PRO=3,0` (MQTT + hex) | **Yes** | Custom broker with hex payload |
| `AT+PRO=3,3` (MQTT + ThingsBoard) | **No** — reverts to `broker.hivemq.com` on reboot | **Never use for custom brokers** |
| `AT+PRO=3,1` (ThingSpeak) | Likely rewrites platform defaults | Avoid unless targeting ThingSpeak |

**Rule:** for `vakt.systemat.is`, Railway, or any private broker, always use `AT+PRO=3,5` and verify `SERVADDR` after `ATZ`.

---

## Concrete triggers

1. **`AT+PRO=3,3`** — documented to reconfigure default server settings; reboot then restores `broker.hivemq.com`.
2. **`ATZ` or Reset button** — on v1.1.4 this alone is enough to rewrite the hostname; on v1.2.1 it is safe only if `PRO=3,5` (or `3,0`) is set.
3. **`AT+FDR` / `AT+FDR1`** — full/partial factory reset; restores factory/demo defaults. On fresh v1.2.1 firmware, runtime config resets to `PRO=2,0`, `TDC=7200`, `SERVADDR=NULL`, etc.
4. **Explicit `AT+SERVADDR=broker.hivemq.com,1883`** — accidental paste from Dragino examples.
5. **MQTT/JSON downlink** — `{"Config":"[AT+SERVADDR=broker.hivemq.com,1883;ATZ]"}` can rewrite the server remotely. Risk is amplified if `SUBTOPIC=#` is subscribed on a reachable public broker.

---

## `BKDNS` mechanics — why it dials HiveMQ even when the hostname looks private

`AT+BKDNS` saves the last resolved IP and uses it if the next DNS resolution fails.

```text
AT+BKDNS=1,0,<ip>,<port>   # disable dynamic update; cache <ip> as fallback
AT+BKDNS=2,<hours>,<ip>,<port>  # periodic re-resolve + cache
AT+BKDNS=1,0,NULL          # clear / default style
```

Typical failure path:

1. Device once used `AT+SERVADDR=broker.hivemq.com,1883`.
2. Resolve succeeds → `BKDNS` caches a HiveMQ IP (e.g. `3.127.172.15`, `18.198.118.51`, `52.59.36.109`).
3. Operator sets `AT+SERVADDR=<private-host>,1883` but leaves the HiveMQ IP in `BKDNS`, or DNS to the private host fails.
4. Stack falls back to the cached HiveMQ IP and the uplink reaches the public broker.

`BKDNS` explains **connecting to a HiveMQ IP** even when the hostname string was changed. It does **not** explain `AT+SERVADDR=?` returning the literal hostname `broker.hivemq.com` — that requires a SERVADDR write, factory reload, or downlink.

**Verification:** always read `AT+BKDNS=?` after a broker change and confirm it matches the intended private IP, not a public HiveMQ address.

---

## Remote downlink rewrite risk

Dragino docs describe a JSON downlink that can run AT commands inside a `Config` bracket:

```json
{"Config":"[AT+SERVADDR=broker.hivemq.com,1883;ATZ]"}
```

Anything that can publish to the device’s `SUBTOPIC` can change `SERVADDR`, `PRO`, topics, and reboot the device. Avoid broad subscriptions like `SUBTOPIC=#` on public or shared brokers; use a narrow topic such as `v1/devices/me/attributes` for ThingsBoard, or `dragino/<id>/down` for Railway.

---

## Fix checklist for operators

Use this after firmware updates, profile changes, or any unexpected broker jump.

```text
<6-digit PIN>
AT+PRO=3,5
AT+SERVADDR=<PRIVATE_IP_OR_HOST>,1883
AT+UNAME=<TOKEN_OR_USERNAME>
AT+PWD=NULL
AT+PUBTOPIC=v1/devices/me/telemetry
AT+SUBTOPIC=v1/devices/me/attributes
AT+CLIENT=null
AT+MQOS=1
AT+TLSMOD=0,0
AT+BKDNS=1,0,<PRIVATE_IP>,1883
AT+APN=<CORRECT_APN>
AT+TDC=<SECONDS>
AT+CFG
ATZ
```

After reboot:

```text
AT+SERVADDR=?       # must NOT contain "hivemq"
AT+BKDNS=?          # must be the private IP, not a public HiveMQ address
AT+PRO=?            # must be 3,5 (or 3,0) for custom brokers
AT+CFG
```

**Do not use `AT+PRO=3,3` for custom brokers.** If you must use ThingsBoard payload Type=3, understand that v1.1.4 will revert to `broker.hivemq.com` on every reboot and v1.2.1 still prefers `PRO=3,5` for persistence.

**Prefer IP form** for private brokers if DNS is unreliable. For `vakt.systemat.is`, use `167.235.104.181,1883` (or `vakt.systemat.is,1883` if DNS is trusted). For Railway, use `66.33.22.220,33239`.

**Never leave HiveMQ in `BKDNS`.** Clear it with `AT+BKDNS=1,0,NULL` or pin it to the private IP.

---

## Quick reference: safe vs unsafe profiles

| Profile | Safe for custom broker? | Notes |
|---|---|---|
| `AT+PRO=3,5` | **Yes** | JSON MQTT; keeps custom `SERVADDR` across reboot on v1.2.1 |
| `AT+PRO=3,0` | **Yes** | Hex MQTT; keeps custom `SERVADDR` across reboot |
| `AT+PRO=3,3` | **No** | ThingsBoard payload; rewrites default server, often to `broker.hivemq.com` |
| `AT+PRO=3,1` | **No** | ThingSpeak platform defaults |
| `AT+PRO=2,x` / `4,x` | N/A | UDP/TCP, not MQTT |

---

## Sources

- Consolidated from former `archive/research/` HiveMQ reports (see `archive/research/README.md`)
- `docs/LLM_SENSOR_SETUP_MANUAL.md` §6.3
- Dragino PS-CB-NA docs: https://docs.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/
