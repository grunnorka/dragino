# BG95-M2 MQTT AT Reference

Reference for the Quectel BG95-M2 (and BG95/BG77/BG600L-M3 family) MQTT AT command layer. The modem is on LPUART1 at 115200 8N1. This doc is written for the open-source Dragino PS-CB-NA firmware replacement; the actual code lives in another tree and is **not** modified here.

## Source documents

| Document | Version | Date | Source |
|---|---|---|---|
| *Quectel BG95&BG77&BG600L Series MQTT Application Note* | V1.1 | 2020-05-28 | https://forums.quectel.com/uploads/short-url/3QD9Ff2mYvcmgPS3H7kRPP59frS.pdf (also mirrored at https://www.dragino.com/downloads/downloads/datasheet/other_vendors/BG95/Software/Quectel_BG95&BG77&BG600L_Series_MQTT_Application_Note_V1.1.pdf) |
| *Quectel BG95&BG77&BG600L Series TCP/IP Application Note* | V1.2 | 2022-06-15 | https://sixfab.com/wp-content/uploads/2023/05/Quectel_BG95BG77BG600L_Series_TCPIP_Application_Note_V1.2.pdf |
| *Quectel BG95&BG77&BG600L Series AT Commands Manual* | V2.0 | 2020-09-18 | https://sixfab.com/wp-content/uploads/2023/05/Quectel_BG95BG77BG600L_Series_AT_Commands_Manual_V2.0.pdf |

A newer MQTT Application Note V1.2 exists on the Quectel download portal but is behind a login; the public V1.1 mirror contains all the MQTT AT command details used below.

## Minimal MQTT publish sequence

Run after network attach is confirmed (see "Network readiness check" below). Use one `<client_idx>` value, e.g. `0`. All string parameters must be quoted.

```text
AT+QMTCFG="pdpcid",0,1                                      // use PDP context 1 (default)
OK
AT+QMTOPEN=0,"broker.example.com",1883                       // open TCP for MQTT client 0
OK
+QMTOPEN: 0,0                                                // wait for this URC before next step
AT+QMTCONN=0,"ps-cb-001"[,"user"[,"pass"]]                   // send MQTT CONNECT
OK
+QMTCONN: 0,0,0                                              // wait for result=0, ret_code=0
AT+QMTPUB=0,1,1,0,"sensor/data"                              // QoS1, no retain, fixed or variable form
> { "t": 25.4 }<0x1A>                                        // wait for '>', then payload + Ctrl-Z
OK
+QMTPUB: 0,1,0                                               // wait for publish URC before sleeping
AT+QMTDISC=0                                                 // clean MQTT disconnect
OK
+QMTDISC: 0,0
AT+QMTCLOSE=0                                                // close TCP
OK
+QMTCLOSE: 0,0
```

Timeouts: `QMTOPEN` is network-dependent; `QMTCONN` uses `<pkt_timeout>` (default 5 s); `QMTPUB` uses `<pkt_timeout> × <retry_times>` (default 15 s). Do **not** send the next command until the required URC has arrived.

## Network readiness check

Do these before any MQTT step. The TCP/IP stack and MQTT client reuse the same PDP context.

| Check | Command | Expected response |
|---|---|---|
| PS attached | `AT+CGATT?` | `+CGATT: 1` |
| EPS registered | `AT+CEREG?` | `+CEREG: <n>,1` or `5` (home/roaming) |
| PDP context active | `AT+CGACT?` | `+CGACT: <cid>,1` |
| IP address assigned | `AT+CGPADDR=<cid>` | `+CGPADDR: <cid>,"<ip>"` |

`<cid>` is the context the MQTT client is bound to (`AT+QMTCFG="pdpcid"`, default 1). If the context is not active, use `AT+CGACT=1,<cid>` (or `AT+QIACT=<cid>` for the Quectel TCP/IP stack) before `QMTOPEN`. `QMTOPEN` result code `3` specifically means *Failed to activate PDP*.

## AT+QMTCFG — configure optional MQTT parameters

All sub-commands share `<client_idx>` range `0–5`. Settings are not saved across reboots. Maximum response time: 300 ms.

| Sub-command | Syntax | Parameters | Defaults / ranges |
|---|---|---|---|
| version | `AT+QMTCFG="version",<client_idx>[,<vsn>]` | `<vsn>`: `3` = MQTT v3.1, `4` = v3.1.1 | Default v3.1.1 |
| pdpcid | `AT+QMTCFG="pdpcid",<client_idx>[,<cid>]` | `<cid>` PDP context: `1–16` | Default `1` |
| ssl | `AT+QMTCFG="ssl",<client_idx>[,<SSL_enable>[,<ctx_index>]]` | `<SSL_enable>`: `0` plain TCP, `1` TLS; `<ctx_index>`: `0–5` | Default plain TCP |
| keepalive | `AT+QMTCFG="keepalive",<client_idx>[,<keep_alive_time>]` | `0–3600` seconds | Default `120` |
| session | `AT+QMTCFG="session",<client_idx>[,<clean_session>]` | `0` = store session, `1` = clean | Default `1` |
| timeout | `AT+QMTCFG="timeout",<client_idx>[,<pkt_timeout>[,<retry_times>[,<timeout_notice>]]]` | `<pkt_timeout>` `1–60` s, `<retry_times>` `0–10`, `<timeout_notice>` `0/1` | Defaults `5`, `3`, `0` |
| will | `AT+QMTCFG="will",<client_idx>[,<will_fg>[,<will_qos>,<will_retain>,<will_topic>,<will_message>]]` | `<will_fg>` `0/1`; if `1`, all four following params are mandatory; topic/msg max 255 bytes | — |
| recv/mode | `AT+QMTCFG="recv/mode",<client_idx>[,<msg_recv_mode>[,<msg_len_enable>]]` | `<msg_recv_mode>` `0`=payload in URC, `1`=not in URC; `<msg_len_enable>` `0/1` | — |
| aliauth | `AT+QMTCFG="aliauth",<client_idx>[,<product_key>,<device_name>,<device_secret>]` | AliCloud only; when set, `QMTCONN` username/password can be omitted | — |

Notes from the manual:

- `clean_session=0` only works if the server supports it.
- If SSL is enabled, `<ctx_index>` must be supplied and the context must be configured with `AT+QSSLCFG` (see TLS section below).
- The `timeout` settings also apply to `QMTCONN`, `QMTSUB`, `QMTUNS`, and `QMTPUB`.

## AT+QMTOPEN / AT+QMTCLOSE — TCP layer for MQTT

### Open

```text
AT+QMTOPEN=<client_idx>,<host_name>,<port>
```

- `<client_idx>`: `0–5`.
- `<host_name>`: IP address or domain name, max 100 bytes.
- `<port>`: `0–65535` (practically `1–65535`).

Response is `OK`, then the URC:

```text
+QMTOPEN: <client_idx>,<result>
```

| `<result>` | Meaning |
|---|---|
| `-1` | Failed to open network |
| `0` | Network opened successfully |
| `1` | Wrong parameter |
| `2` | MQTT client identifier is occupied |
| `3` | Failed to activate PDP |
| `4` | Failed to parse domain name |
| `5` | Network connection error |

Maximum response time is *determined by network* — wait for the URC, not just `OK`. Result `3` means the PDP context is missing/inactive; result `4` means DNS failed or hostname was invalid.

### Close

```text
AT+QMTCLOSE=<client_idx>
```

Response: `OK`, then `+QMTCLOSE: <client_idx>,<result>` where `<result>` is `-1` (failed) or `0` (success). Max response time: 300 ms.

## AT+QMTCONN — MQTT CONNECT

```text
AT+QMTCONN=<client_idx>,<clientID>[,<username>[,<password>]]
```

- `<clientID>` is the MQTT client ID string (must be unique on the broker; a duplicate ID forces the older connection to drop).
- `<username>` / `<password>` are optional unless AliCloud `aliauth` is used.

Response: `OK`, then:

```text
+QMTCONN: <client_idx>,<result>[,<ret_code>]
```

| `<result>` | Meaning |
|---|---|
| `0` | Packet sent successfully and ACK received from server |
| `1` | Packet retransmission |
| `2` | Failed to send packet |

| `<ret_code>` | Meaning |
|---|---|
| `0` | Connection Accepted |
| `1` | Connection Refused: Unacceptable Protocol Version |
| `2` | Connection Refused: Identifier Rejected |
| `3` | Connection Refused: Server Unavailable |
| `4` | Connection Refused: Bad User Name or Password |
| `5` | Connection Refused: Not Authorized |

Max response time: `<pkt_timeout>` (default 5 s). A *result* of `0` with a *ret_code* of `1–5` is still a failed connect; M4 must verify both fields. `AT+QMTCONN?` returns `<client_idx>,<state>` where `3` means connected.

## AT+QMTPUB — publish a message

Two forms. The `msgID` is `0` only when `<qos>=0`; otherwise `1–65535`.

### Variable-length form

```text
AT+QMTPUB=<client_idx>,<msgID>,<qos>,<retain>,<topic>
> <payload><0x1A>
```

After the `>` prompt, send the payload, then the byte `0x1A` (Ctrl-Z) to transmit. `0x1B` (Esc) cancels.

### Fixed-length form

```text
AT+QMTPUB=<client_idx>,<msgID>,<qos>,<retain>,<topic>,<msglen>
> <exactly msglen bytes>
```

After the `>` prompt, the modem expects exactly `<msglen>` bytes. No Ctrl-Z is required; the data is sent automatically once the count is reached.

### Response

```text
OK
+QMTPUB: <client_idx>,<msgID>,<result>[,<value>]
```

| `<result>` | Meaning |
|---|---|
| `0` | Packet sent successfully and ACK received from server (QoS 0 does not require ACK) |
| `1` | Packet retransmission |
| `2` | Failed to send packet |

If `<result>` is `1`, `<value>` is the retransmission count. Max response time: `<pkt_timeout> × <retry_times>` (default 15 s). Max payload length: **4096 bytes**.

Important: `OK` from the command only means the modem is ready to accept payload; the actual publish outcome is in the `+QMTPUB` URC. Do not start another publish or go to sleep before that URC arrives. The modem allows up to **5 packets in flight** simultaneously (`inflight` window); exceeding that can drop or block publishes.

## AT+QMTDISC — clean MQTT disconnect

```text
AT+QMTDISC=<client_idx>
```

Response: `OK`, then `+QMTDISC: <client_idx>,<result>` where `<result>` is `-1` (failed) or `0` (success). Max response time: 300 ms. Use this before `QMTCLOSE` for a clean teardown.

## +QMTSTAT — MQTT link-layer failure URC

Fires when the MQTT link-layer state changes and the client closes the connection:

```text
+QMTSTAT: <client_idx>,<err_code>
```

| `<err_code>` | Meaning | Recommended action |
|---|---|---|
| `1` | Connection closed or reset by peer | Reopen with `QMTOPEN` |
| `2` | PINGREQ timed out / failed | Deactivate PDP, reactivate, reopen |
| `3` | CONNECT packet timed out / failed | Check credentials/ClientID, reopen |
| `4` | CONNACK timed out / failed | Check credentials/ClientID, reopen |
| `5` | Client sent DISCONNECT, server closing | Normal |
| `6` | Client closed due to persistent send failure | Reopen, check data / network |
| `7` | Link not alive / server unavailable | Verify server is reachable |
| `8–255` | Reserved | — |

## +QMTRECV — downlink (brief)

Two URC forms:

```text
+QMTRECV: <client_idx>,<msgID>,<topic>,<payload>
+QMTRECV: <client_idx>,<recv_id>
```

The first form reports the received payload directly (default when `recv/mode` is `0`). The second form means the message was stored in a buffer; use `AT+QMTRECV=<client_idx>[,<recv_id>]` to read up to 5 buffered messages. `<recv_id>` range is `0–4`.

## TCP/IP + DNS support commands

These are the supporting commands used to get IP connectivity before `QMTOPEN`. On BG95 the MQTT client can resolve hostnames internally, but the PDP context and DNS must be ready.

| Command | Purpose | Key parameters |
|---|---|---|
| `AT+QICSGP=<contextID>[,<context_type>,<APN>[,<username>,<password>[,<authentication>]]]` | Configure PDP context APN/auth | `<contextID>` `1–16`; `<context_type>` `1`=IPv4, `2`=IPv6, `3`=IPv4v6; `<authentication>` `0`=None, `1`=PAP, `2`=CHAP, `3`=PAP or CHAP |
| `AT+QIACT=<contextID>` | Activate the PDP context | Max 150 s; max 3 contexts under Cat-M/EGPRS, 2 under Cat-NB2 |
| `AT+QIDNSCFG=<contextID>[,<pridnsaddr>[,<secdnsaddr>]]` | Set DNS servers for a context | Must be called after `QIACT` |
| `AT+QIDNSGIP=<contextID>,<host_name>` | Resolve a hostname manually | Response: `+QIURC: "dnsgip",<result>,<IP_count>,<DNS_ttl>[,<host_IP_addr>...]`; max 60 s |

For `QMTOPEN`, a domain name is acceptable (max 100 bytes in the MQTT note). If DNS fails, `QMTOPEN` returns `+QMTOPEN: <client_idx>,4`.

## TLS/SSL note (plain TCP planned)

The current design uses plain TCP through the Railway proxy. To use TLS later, configure it in this order:

```text
AT+QMTCFG="ssl",<client_idx>,1,<ctx_index>       // enable SSL, select context 0-5
AT+QFUPL="cacert.pem",<len>,100                  // upload CA cert to UFS
AT+QSSLCFG="cacert",<ctx_index>,"cacert.pem"
AT+QSSLCFG="seclevel",<ctx_index>,2
AT+QSSLCFG="sslversion",<ctx_index>,4
AT+QSSLCFG="ignorelocaltime",<ctx_index>,1
```

Full details are in the *Quectel BG95 SSL Application Note* (referenced by the AT Commands Manual but not downloaded here).

## Firmware version differences

The official manuals do **not** contain a table that maps `ATI` or `AT+QGMR` version strings to MQTT support. However, a Quectel forum thread (secondary source) reports that **QuecPython firmware builds do not expose the MQTT AT commands**. For example, the build `BG95M3LAR02A03_31.201.31.201` was identified as a QuecPython image where `AT+QMTCFG`, `AT+QMTOPEN`, and `AT+QMTCONN` all return `ERROR`. Ensure the module is running standard modem firmware before relying on the command set in this document. The manuals only state: *"See the firmware release notes of the corresponding module to check whether the function is supported."*

## Pitfalls & stock-firmware bug forensics

These are the documented behaviors that can produce the symptom *TCP connected, but no data is ever published*.

1. **MQTT CONNECT failed silently.** A successful `+QMTOPEN` only means the TCP socket is open. The driver must wait for `+QMTCONN` and verify **both** `<result>` = `0` *and* `<ret_code>` = `0`. If `ret_code` is `1–5`, the broker refused the CONNECT and any subsequent `QMTPUB` will fail or be ignored. The stock firmware may have skipped this check.

2. **QMTPUB prompt / length mishandling.** The `OK` after `AT+QMTPUB` only opens the data prompt. The driver must wait for the `>` character, send the exact payload bytes, and then send `0x1A` (Ctrl-Z) in the variable form. If the fixed-length form is used, the byte count must exactly match `<msglen>`. Sending too few or too many bytes, or sending Ctrl-Z in the fixed-length form, can cause the modem to swallow the payload without publishing it.

3. **Treating `OK` as publish success.** The real outcome is the `+QMTPUB` URC. If the host goes to sleep, closes the connection, or issues another command before the URC arrives, an unacknowledged QoS 1/2 packet may be dropped or the modem may report `+QMTSTAT` later. The default publish timeout is `pkt_timeout × retry_times` = 15 s on NB-IoT networks, so the host must stay awake and keep the UART open until the URC is received.

Additional failure modes to log:

- **Duplicate ClientID** — the broker disconnects the older session; a new `QMTCONN` with the same ID may fail or inherit unexpected session state.
- **Inflight window exhausted** — the modem allows 5 unacknowledged packets; a 6th publish may be rejected or queued forever.
- **PDP deactivation** — if the context drops, `QMTOPEN` returns `3` or `+QMTSTAT: <client_idx>,7` fires. The driver must reactivate the PDP context before reopening MQTT.
- **Keep-alive timeout** — if the host sleeps longer than `1.5 × keep_alive_time` (default 180 s), the broker may disconnect the client. Set `keep_alive_time` to match the wake interval.
- **Clean session mismatch** — `AT+QMTCFG="session",0` only works if the broker supports it; otherwise the CONNECT may be refused.
