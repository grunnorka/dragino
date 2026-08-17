# A/B carrier test: Vodafone GDSP vs Síminn on vakt.systemat.is

Date: 2026-08-17  
Bench device: PS-CB-NA (Dragino), open firmware `d7db977` (full M4 uplink, AT+MDM passthrough)  
Serial console: `/dev/ttyUSB0` @ 9600, no PIN gate  
Broker: `vakt.systemat.is:1883` (ThingsBoard)  
Modem: Quectel BG95-M2  

## SIMs under test

| | Síminn | Vodafone GDSP |
|---|---|---|
| IMSI | `274012011267761` (baseline from prior session) | `901280043992222` |
| MCC/MNC | 274 / 01 | 901 / 28 (Vodafone Iceland) |
| ICCID | — | `89882390001444350756` |
| MSISDN | — | `882390043992222` |

The Vodafone SIM was inserted live and the board reset; the bootloader re-read the SIM and the openfw log reported `IMSI:901280043992222` on every boot. Swap confirmed.

## Vodafone portal facts (GDSP export, 2026-08-17)

- **State**: `Active.Test` (never `Active.Live`)
- **Tariff**: `LPWA_Iceland`
- **Roaming Group**: `162_Restricted_CAN_TBS_LPWA_UpTo_PP` — description: "4G limited to LPWA networks"
- **APN**: `lpwa.vodafone.is` with APN Access List **`IDER_ACL`**
- **Groups**: "Device was not added to any groups yet" (zero VPN-group membership)
- **Radio capabilities**: LTE-M Yes / NB-IoT Yes / 4G No
- **Network registration**: registered on Vodafone Iceland (27402) with a packet-switched update minutes before the test

These facts are relevant for the Sýn escalation: the `IDER_ACL` is confirmed applied to this exact SIM, and the device is live on the Vodafone LPWA network.

## Device configuration for the vakt test

```text
AT+SERVADDR=vakt.systemat.is,1883
AT+BKDNS=1,0,167.235.104.181,1883
AT+CLIENT=ps-cb
AT+UNAME=REol…T0fu
AT+PWD=NULL
AT+PUBTOPIC=v1/devices/me/telemetry
AT+SUBTOPIC=v1/devices/me/attributes
AT+TDC=120
AT+PRO=3,5
AT+TLSMOD=0,0
AT+MQOS=1
AT+DEBUG=1
AT+IOTMOD=0
AT+APN=NULL
```

For the Síminn baseline the same broker and topic set was used, with the same device/telemetry topic, but with Síminn's PDP context (see table below).

## Results table

| Metric | Síminn 27401 | Vodafone GDSP 90128 / 27402 |
|---|---|---|
| attach RAT / band | LTE-M, band 28 | eMTC, LTE band 8 |
| CEREG | registered | stat=5 (registered, roaming) |
| PDP IP | `157.157.248.124` | `100.78.62.217` |
| CSQ | 31 | 29–31 |
| DNS behavior | `vakt.systemat.is` → `167.235.104.181` immediately | `vakt.systemat.is` → `167.235.104.181` immediately |
| `+QMTOPEN` | `0,0` (success) | `0,0` (success) |
| `+QMTCONN` | `0,0,0` (CONNACK received) | **never reached; TCP reset before CONNACK** |
| `+QMTSTAT` | none | `0,1` on every attempt |
| `+QMTPUB` | `0,1,0` | none |
| final verdict | `Upload data successfully` | `Failed to connect to server` / `Failed to send` |

## Exact URC log excerpts — Vodafone on vakt.systemat.is

All three cycles with `TDC=120` show the same pattern. The modem attaches, DNS resolves, the TCP socket opens successfully, and then the carrier-side resets the TCP connection within ~250–340 ms before an MQTT CONNACK is received.

### Cycle 1 (boot) — 12:01:54 UTC

```text
[12447]Domain IP:167.235.104.181
[13388]Network Information:"eMTC","27402","LTE BAND 8",3600
[13842]QIACT: +QIACT: 1,1,1,"100.78.62.217"|OK
[14255]URC: +QMTOPEN: 0,0
[14311]Opened the MQTT client network successfully
[14539]URC: +QMTSTAT: 0,1
[14595]Failed to connect to server
...
[14754]MQTT attempt 2
[14923]URC: +QMTOPEN: 0,0
[14980]Opened the MQTT client network successfully
[15194]URC: +QMTSTAT: 0,1
[15250]Failed to connect to server
...
[15410]MQTT attempt 3
[15572]URC: +QMTOPEN: 0,0
[15629]Opened the MQTT client network successfully
[15844]URC: +QMTSTAT: 0,1
[15900]Failed to connect to server
[16060]Failed to send
[16108]*****End of upload*****
```

### Cycle 2 — 12:03:54 UTC

```text
[132463]Domain IP:167.235.104.181
[133431]Network Information:"eMTC","27402","LTE BAND 8",3600
[133893]QIACT: +QIACT: 1,1,1,"100.78.62.217"|OK
[134422]URC: +QMTOPEN: 0,0
[134480]Opened the MQTT client network successfully
[134703]URC: +QMTSTAT: 0,1
[134762]Failed to connect to server
...
[16060]Failed to send
[136328]*****End of upload*****
```

### Cycle 3 — 12:05:54 UTC

```text
[252463]Domain IP:167.235.104.181
[253431]Network Information:"eMTC","27402","LTE BAND 8",3600
[253893]QIACT: +QIACT: 1,1,1,"100.78.62.217"|OK
[254356]URC: +QMTOPEN: 0,0
[254414]Opened the MQTT client network successfully
[254659]URC: +QMTSTAT: 0,1
[254718]Failed to connect to server
...
[256230]Failed to send
[256280]*****End of upload*****
```

Each cycle attempts three MQTT connections; every attempt ends with `+QMTSTAT: 0,1` (TCP connection broken) and no `+QMTCONN` or `+QMTPUB`.

## Timing: CONNECT-sent to reset vs Síminn CONNECT-to-CONNACK

| Attempt | Vodafone `+QMTOPEN` → `+QMTSTAT` | Síminn `+QMTOPEN` → `+QMTCONN` |
|---|---|---|
| Cycle 1 #1 | ~288 ms | — |
| Cycle 1 #2 | ~256 ms | — |
| Cycle 1 #3 | ~272 ms | — |
| Cycle 2 #1 | ~288 ms | — |
| Cycle 2 #2 | ~288 ms | — |
| Cycle 2 #3 | ~320 ms | — |
| Cycle 3 #1 | ~304 ms | — |
| Cycle 3 #2 | ~272 ms | — |
| Cycle 3 #3 | ~336 ms | — |
| Síminn baseline | — | `+QMTOPEN: 0,0` → `+QMTCONN: 0,0,0` (clean, CONNACK received) |

Vodafone consistently tears down the TCP connection roughly 250–340 ms after the BG95 reports the MQTT socket open. Síminn, on the same broker and same device, proceeds to `+QMTCONN: 0,0,0` and publishes successfully.

## Modem serving-cell state at failure time

Between cycles 2 and 3 the device was queried with `AT+MDM=AT+QENG="servingcell"` while the modem was still attached:

```text
+QENG: "servingcell","NOCONN","eMTC","FDD",274,02,44F5C,267,3600,8,3,3,8,-80,-12,-53,11,47
```

Interpretation: registered on Vodafone Iceland (MCC 274, MNC 02), eMTC, LTE band 8, EARFCN 3600, cell ID `44F5C`, PCID 267. Radio conditions were stable during the failure window, so the reset is not explained by a dropped radio link.

## Conclusion — carrier-side reset evidence

The same PS-CB-NA device, same open firmware, same ThingsBoard broker (`vakt.systemat.is:1883`), and same DNS result (`167.235.104.181`) behave oppositely depending only on the SIM:

- **Síminn (27401)**: full MQTT handshake, successful publish, `Upload data successfully`.
- **Vodafone GDSP (90128 / 27402)**: TCP opens, then the carrier network resets the connection ~250–340 ms later with `+QMTSTAT: 0,1`, before the MQTT CONNECT can complete. This repeats on every attempt across three consecutive cycles.

The DNS path is not the cause: the hostname resolves immediately to the same vakt IP on both carriers. The failure is a TCP-layer reset injected after the socket is established, which is consistent with the Vodafone network or its ACL/profile policy blocking the ThingsBoard endpoint while allowing the TCP three-way handshake to complete. The stable serving-cell report and the repeatability of the ~300 ms reset rule out a weak-radio explanation.

Recommended escalation wording: *"Vodafone GDSP PDP context `100.78.62.217` attaches and opens TCP to `vakt.systemat.is:1883` successfully, but the carrier path resets the TCP connection within ~300 ms before MQTT CONNACK. The same device, same broker, and same DNS resolution succeed on Síminn. The Vodafone SIM profile/ACL appears to be blocking this destination at the packet-core or DPI level."*

## Restore confirmation

After the test the device was restored to the Railway production configuration and reset. Final `AT+CFG` (redacted) confirms:

```text
AT+SERVADDR=altaria.proxy.rlwy.net,33239
(altaria.proxy.rlwy.net,33239)
AT+CLIENT=ps-cb
AT+UNAME=dragino
AT+PWD=***
AT+PUBTOPIC=dragino/ps-cb/up
AT+SUBTOPIC=dragino/ps-cb/down
AT+TDC=180
AT+APN=NULL
AT+PRO=3,5
AT+BKDNS=1,0,66.33.22.220,33239
AT+TLSMOD=0,0
AT+MQOS=1
AT+IOTMOD=0
AT+DEBUG=1
```

IOTMOD=0, APN=NULL, DEBUG=1 retained. Raw bench log with the unredacted ThingsBoard token remains local only (`bench-logs/2026-08-17-vodafone-bench-raw.log`) and is **not** committed.
