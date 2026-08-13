# PPS-CB-NA – NB-IoT/LTE-M Analog Sensor (structured extract)

**Primary source:** workspace companion Markdown  
`PPS-CB-NA – NB-IoT_LTE-M Analog Sensor.md` (~4.5 MB, OCR/export of the user manual; PDF on disk is a 9 KB stub and not usable).

**Also used for stack-level commands not listed in the product AT table:** Dragino NB-IoT general configuration / protocol-stack notes (`AT+BKDNS`, `broker.hivemq.com` SERVADDR example, `AT+MQOS` defaults) — same family as PS-CB-NA firmware stack **D-BG95-*** / CB models.

**Product:** Dragino PS-CB-NA (NB-IoT/LTE-M Analog Sensor)  
**Uplink protocols:** MQTT, MQTTs, UDP, TCP, CoAP  
**Module:** BG95-NGFF

---

## 1. Serial unlock

| Command | Purpose |
|---|---|
| Six-digit PIN from box label, or `AT+PIN=xxxxxx` | Enter AT console after UART/BLE connect |
| `AT+PWORD=xxxxxx` | Change system password (6 chars, lowercase on CB) |
| Commands need a trailing newline | |

---

## 2. Payload / protocol profile (`AT+PRO`)

Syntax: `AT+PRO=<protocol>,<payloadType>`

| Protocol | Meaning |
|---|---|
| 1 | CoAP |
| 2 | UDP |
| 3 | MQTT |
| 4 | TCP |

| Payload type | Meaning |
|---|---|
| 0 | HEX |
| 1 | ThingSpeak |
| 3 | ThingsBoard |
| 5 | JSON |

Examples from the manual:

```text
AT+PRO=2,0    // UDP + hex
AT+PRO=2,5    // UDP + JSON
AT+PRO=3,0    // MQTT + hex
AT+PRO=3,1    // MQTT + ThingSpeak
AT+PRO=3,3    // MQTT + ThingsBoard
AT+PRO=3,5    // MQTT + JSON
AT+PRO=4,0    // TCP + hex
AT+PRO=4,5    // TCP + JSON
```

### Critical ThingsBoard warning

Manual §2.2.3 (ThingsBoard Payload Type=3):

> Type3 payload special design for ThingsBoard, **it will also configure other default server to ThingsBoard.**

So **`AT+PRO=3,3` rewrites server-related defaults** (not only payload format). After any `AT+PRO=…`, re-check `AT+SERVADDR=?` / `AT+CFG` and restore a private broker if needed.

Demo / docs MQTT host example used elsewhere in the NB stack:

```text
AT+SERVADDR=broker.hivemq.com,1883
```

(spaces are stripped on write).

---

## 3. MQTT / server AT commands (product AT list)

### General

| Command | Role |
|---|---|
| `AT+CFG` | Print all settings |
| `AT+SERVADDR` | Get/Set server address (`host,port`) |
| `AT+TDC` | Uplink interval in **seconds** (default every **2 hours**) |
| `AT+APN` | Cellular APN |
| `AT+PRO` | Protocol + payload profile |
| `AT+DNSCFG` | DNS server |
| `AT+GDNS` | Enable/disable DNS |
| `AT+TLSMOD` | TLS mode (MQTTs) |
| `AT+IPTYPE` | IPv4 / IPv6 |
| `AT+CSQTIME` | Network join search time (minutes) |
| `AT+FDR` | Factory reset **all** parameters |
| `AT+FDR1` | Factory default **except passwords** |
| `ATZ` | MCU reset |

### MQTT management

| Command | Role |
|---|---|
| `AT+CLIENT` | MQTT client ID |
| `AT+UNAME` | MQTT username (often ThingsBoard **device access token**) |
| `AT+PWD` | MQTT password (`NULL` common for token-as-username) |
| `AT+PUBTOPIC` | Publish topic |
| `AT+SUBTOPIC` | Subscribe topic |
| `AT+MQOS` | MQTT QoS (0 / 1 / 2; stack default often 0) |

### Query / set pattern

```text
AT+CMD=?          // get
AT+CMD=<value>    // set
AT+CMD?           // help
```

### Server address

```text
AT+SERVADDR=<host>,<port>
AT+SERVADDR=?
# Example (demo only — do NOT use for private TB):
AT+SERVADDR=broker.hivemq.com,1883
# Private MQTT / ThingsBoard broker:
AT+SERVADDR=<YOUR_TB_HOST>,1883
```

### Uplink interval (TDC)

```text
AT+TDC=<seconds>
AT+TDC=?
AT+TDC=7200     // 2 hours (manual example)
AT+TDC=180      // 3 minutes (project target)
# Downlink 0x01 + 3-byte seconds: 01 00 A8 C0 = AT+TDC=43200
```

Default product behaviour: uplink **every 2 hours** until changed.

---

## 4. ThingsBoard MQTT configuration

### Official Dragino ThingsBoard.Cloud (MQTT integration) pattern

From shared CB/NB server docs (linked from this product manual):

```text
AT+PRO=3,3
AT+SUBTOPIC=<device name>
AT+PUBTOPIC=<device name>
AT+CLIENT=<device name> or User Defined
AT+UNAME=<device name> or User Defined
AT+PWD=<device name> or User Defined
```

Then set `AT+SERVADDR` to the ThingsBoard MQTT endpoint / integration host and port.

### Private ThingsBoard — device access-token pattern (common on self-hosted TB)

Observed / recommended for token auth (not the integration-style device-name topics):

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
```

Use port `8883` + TLS (`AT+TLSMOD=…`) if the broker requires MQTTs.

**After `AT+PRO=3,3`, always re-apply `AT+SERVADDR` to the private host** — Type=3 rewrites default server settings.

---

## 5. `AT+BKDNS` (DNS cache / failover) — stack command

Not listed in the PS-CB-NA product AT table, but present on this firmware family (live devices respond to it). From NB-IoT stack changelog:

```text
AT+BKDNS=?
AT+BKDNS=a,b,c
```

| Param | Meaning |
|---|---|
| `a=1` | Dynamic DNS update **disabled**; IP saved after resolve; on later DNS failure, **last saved IP is used** |
| `a=2` | Dynamic update **enabled**; refresh every `b` hours |
| `b` | Update interval (hours) |
| `c` | Manual / cached IP in same shape as `SERVADDR` (`ip,port`) |

Examples:

```text
AT+BKDNS=1,0                    // disable dynamic update (clear sticky form)
AT+BKDNS=1,0,NULL               // default-style query example in docs
AT+BKDNS=2,1                    // update every 1 hour
AT+BKDNS=2,4,3.69.98.183,1883   // fallback IP if DNS fails
```

**HiveMQ sticky-IP risk:** if `SERVADDR` was once `broker.hivemq.com`, `BKDNS` may cache a public HiveMQ IP. After pointing SERVADDR at private TB, clear/replace BKDNS or the module may still dial HiveMQ when DNS fails.

---

## 6. Factory defaults / resets that rewrite server

| Command | Effect |
|---|---|
| `AT+FDR` | Full factory parameter reset |
| `AT+FDR1` | Factory defaults except passwords |
| `AT+PRO=3,3` (and other PRO profiles) | Can push platform default server settings |
| `AT+PRO=3,1` | ThingSpeak platform defaults |

Do **not** run `AT+FDR` / `AT+FDR1` unless a full wipe is intended.

---

## 7. Cellular / connectivity (MQTT uplink prerequisites)

| Command | Role |
|---|---|
| `AT+APN=<apn>` | Operator APN (e.g. `lpwa.vodafone.is`) |
| `AT+CSQTIME=<minutes>` | Extend network search |
| `AT+GDNS` / `AT+DNSCFG` | DNS enable / DNS servers |
| `AT+QSW` | Power BG95 module on/off |
| `AT+TLSMOD` | TLS for MQTTs |

Network attach is required before MQTT uplink; wrong APN/DNS prevents private-host resolve and increases BKDNS failover to cached HiveMQ IPs.

---

## 8. Verify

```text
AT+CFG
AT+SERVADDR=?
AT+BKDNS=?
AT+PRO=?
AT+UNAME=?
AT+PWD=?
AT+PUBTOPIC=?
AT+SUBTOPIC=?
AT+CLIENT=?
AT+MQOS=?
AT+TLSMOD=?
AT+TDC=?
AT+APN=?
```

Expect private TB host in both `SERVADDR` and `BKDNS` (no `hivemq`).

---

## Source map

| Topic | Where |
|---|---|
| Full companion export | `PPS-CB-NA – NB-IoT_LTE-M Analog Sensor.md` |
| AT list / PRO / TDC / MQTT cmds | Companion MD §3.3, §2.2, §3.4 |
| ThingsBoard Type=3 server rewrite | Companion MD §2.2.3 |
| BKDNS / HiveMQ SERVADDR example / MQOS | NB-IoT general stack docs (CB models) |
| Serial agent handoff | `AT_COMMANDS_HANDOFF.md` |
| HiveMQ incident notes | `HIVEMQ_BROKER_SWITCH.md` |
