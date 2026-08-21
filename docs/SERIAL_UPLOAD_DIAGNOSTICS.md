# Serial upload diagnostics — “Failed to send” vs wrong broker

Serial logs from the Dragino PS-CB-NA can look alarming even when data is reaching the server. Learn the two common failure modes and what to trust.

---

## Two failure modes

### A. Wrong broker / HiveMQ jump — data never reaches private ThingsBoard

The device is configured as a ThingsBoard MQTT client but points at a public broker.

```text
AT+SERVADDR=broker.hivemq.com,1883
AT+BKDNS=1,0,3.127.172.15,1883
AT+PRO=3,3
AT+PUBTOPIC=v1/devices/me/telemetry
AT+SUBTOPIC=v1/devices/me/attributes
```

What you see in the serial log:

```text
Opened the MQTT client network successfully
Successfully connected to the server
Upload data successfully
Failed to send
*****End of upload*****
```

The MQTT connect and publish may appear to succeed because HiveMQ accepts an anonymous/open session. However, the message is published to the **public** broker, not the private ThingsBoard. ThingsBoard never receives the data.

**Distinguishing marks:**

- `AT+SERVADDR=?` contains `broker.hivemq.com` or any unexpected public host.
- `AT+BKDNS=?` holds a public HiveMQ IP (`3.127.x.x`, `18.198.x.x`, `52.59.x.x`).
- Uplink log shows `Resolving domain name...` / `Domain IP:` to a HiveMQ address.
- Private ThingsBoard shows no new telemetry for that device.

### B. Harmless teardown on private ThingsBoard — “Upload data successfully” then “Failed to send” is NOT a publish failure

The device is correctly pointed at the private broker:

```text
AT+SERVADDR=167.235.104.181,1883
AT+BKDNS=1,0,167.235.104.181,1883
AT+PRO=3,3
AT+TDC=180
AT+PUBTOPIC=v1/devices/me/telemetry
AT+SUBTOPIC=v1/devices/me/attributes
```

What you see on every controlled cycle:

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
```

`Upload data successfully` and `Subscribe to topic successfully` mean the telemetry and subscribe paths both worked. The later `Failed to close TCP connection` / `Failed to send` is a **post-success teardown or secondary send step** in the firmware, not the MQTT publish failing.

**Distinguishing marks:**

- `AT+SERVADDR=?` is the intended private IP or host.
- `AT+BKDNS=?` matches the same private IP.
- The log shows `Upload data successfully` before `Failed to send`.
- ThingsBoard is receiving the telemetry.

---

## How to distinguish

| Check | Mode A: wrong broker | Mode B: harmless teardown |
|---|---|---|
| `AT+SERVADDR=?` | `broker.hivemq.com` or public host | Private IP/host |
| `AT+BKDNS=?` | Public HiveMQ IP | Private IP |
| `Domain IP:` in uplink | Public HiveMQ | Private broker |
| ThingsBoard ingestion | **No data** | Data arrives |
| First success line | `Upload data successfully` may appear | `Upload data successfully` appears |
| `Failed to send` meaning | Data left the device but went to wrong broker | Data already sent; TCP close failed later |

---

## What to trust

Trust these two things together:

1. **`Upload data successfully`** in the serial log — the MQTT PUBLISH was accepted by the broker currently in `SERVADDR`.
2. **Server ingestion** — the target platform (ThingsBoard, Railway dashboard, etc.) actually shows the new telemetry.

Do **not** treat `Failed to send` alone as a publish failure. It is common on private ThingsBoard after a successful upload and subscribe. It is also common on HiveMQ after the first publish, but there the real problem is the wrong broker, not the teardown message.

---

## Operator actions

When you see `Failed to send`:

1. Check `AT+SERVADDR=?` and `AT+BKDNS=?` first.
2. If either contains HiveMQ, fix the broker jump before worrying about the teardown message.
3. If both are private and `Upload data successfully` appeared, verify the server received the data.
4. Only investigate radio/modem issues if the broker is correct **and** no `Upload data successfully` appears **and** the server shows no data.

---

## Sources

- Consolidated from former `archive/research/` debug reports (see `archive/research/README.md`)
- `docs/HIVEMQ_AND_BROKER_PERSISTENCE.md`
