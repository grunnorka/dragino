# AT Commands Quickref — PS-CB-NA

Slim reference for serial operators. Use with a USB-TTL adapter at 9600 8N1. Always unlock first.

---

## Unlock

```text
<6-digit PIN from box label>
# or
AT+PIN=<6-digit>
```

If the device is sleeping, press the **ACT** button for 1–3 s or wait for the next TDC cycle.

---

## Recommended profile for custom brokers

For private ThingsBoard or Railway, use **MQTT + JSON** (`PRO=3,5`). Never use `PRO=3,3` for custom brokers — it rewrites the default server to `broker.hivemq.com` on reboot on older firmware.

```text
AT+PRO=3,5
```

`PRO=3,0` (MQTT + hex) also keeps a custom `SERVADDR` across reboot on v1.2.1.

---

## Private ThingsBoard (token style)

```text
AT+PRO=3,5
AT+SERVADDR=167.235.104.181,1883
AT+UNAME=<DEVICE_ACCESS_TOKEN>
AT+PWD=NULL
AT+PUBTOPIC=v1/devices/me/telemetry
AT+SUBTOPIC=v1/devices/me/attributes
AT+CLIENT=null
AT+MQOS=1
AT+TLSMOD=0,0
AT+BKDNS=1,0,167.235.104.181,1883
AT+APN=<CORRECT_APN>
AT+TDC=180
ATZ
```

Use `vakt.systemat.is,1883` instead of the IP if DNS is trusted. For TLS, switch to port `8883` and set `AT+TLSMOD` per broker requirements.

---

## Railway MQTT

```text
AT+PRO=3,5
AT+SERVADDR=66.33.22.220,33239
AT+UNAME=dragino
AT+PWD=<MQTT_PASS>
AT+PUBTOPIC=dragino/<device-id>/up
AT+SUBTOPIC=dragino/<device-id>/down
AT+CLIENT=<device-id>
AT+MQOS=1
AT+TLSMOD=0,0
AT+BKDNS=1,0,66.33.22.220,33239
AT+APN=<CORRECT_APN>
AT+TDC=180
ATZ
```

TLS is **refused** by the Railway TCP proxy; keep `AT+TLSMOD=0,0`. The proxy is plaintext-only and assigns a high public port (`33239`); Railway cannot expose public `1883`.

---

## Common AT commands

| Goal | Command |
|---|---|
| Set MQTT host/port | `AT+SERVADDR=<host>,<port>` |
| Set username / token | `AT+UNAME=<token_or_user>` |
| Set password | `AT+PWD=<password>` or `AT+PWD=NULL` |
| Set publish topic | `AT+PUBTOPIC=<topic>` |
| Set subscribe topic | `AT+SUBTOPIC=<topic>` |
| Set QoS | `AT+MQOS=0` / `1` / `2` |
| Set client ID | `AT+CLIENT=<id>` or `null` |
| DNS cache / failover | `AT+BKDNS=1,0,<ip>,<port>` |
| Clear DNS cache | `AT+BKDNS=1,0,NULL` |
| Uplink interval (seconds) | `AT+TDC=<seconds>` |
| Clock-log interval | `AT+CLOCKLOG=1,65535,<min>,<count>` |
| TLS off | `AT+TLSMOD=0,0` |
| Dump all settings | `AT+CFG` |
| Reboot MCU | `ATZ` |
| Partial factory reset | `AT+FDR1` (keeps passwords; resets runtime profile) |
| Full factory reset | `AT+FDR` |

---

## Clear HiveMQ from `BKDNS`

```text
AT+BKDNS=1,0,NULL
AT+BKDNS=?
```

If readback still shows a public HiveMQ IP (`3.127.x.x`, `18.198.x.x`, `52.59.x.x`), re-set it to the private IP and resolve again.

---

## Verify before declaring success

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

- `SERVADDR` is the intended private broker, **not** `broker.hivemq.com`.
- `BKDNS` matches the private IP; no public HiveMQ address.
- `PRO` is `3,5` (or `3,0`) for custom brokers.
- Topics and auth match the target platform.
- After `ATZ`, the same settings survive reboot.

---

## Minimal fix: HiveMQ → private broker

Assume the device is unlocked and topics/auth are already correct; only the broker is wrong:

```text
AT+SERVADDR=167.235.104.181,1883
AT+BKDNS=1,0,167.235.104.181,1883
AT+PRO=3,5
AT+CFG
```

If the broker keeps reverting after `ATZ`, switch to `PRO=3,5` and re-apply the full sequence above.

---

## Do / Don't

| Do | Don't |
|---|---|
| Use `AT+PRO=3,5` for custom brokers | Use `AT+PRO=3,3` for custom brokers |
| Re-set `SERVADDR` and `BKDNS` after every `AT+PRO` or `ATZ` | Assume the broker survived a reboot without checking |
| Pin `BKDNS` to the private IP | Leave a public HiveMQ IP in `BKDNS` |
| Use a narrow `SUBTOPIC` | Use `SUBTOPIC=#` on public or shared brokers |
| Verify with `AT+CFG` and a server-side ingestion check | Trust `Failed to send` alone as a failure signal |
| Run `AT+FDR1` only when a partial reset is intended | Run `AT+FDR` casually — it wipes all parameters |
