# PS-CB-NA wordbook

Reverse-engineering dictionary of text the sensor speaks: AT commands, serial
debug lines, payload field names, and fixed replies.

Sources:

- Firmware string table: `firmware/PS-CB-NA_v1.2.1.bin` (v1.2.1 / D-BG95-004)
- Bootloader: `firmware/DRAGINO_NB_bootloader_v1.3.bin`
- Captured UART logs in `logs/`
- Wiki product page: <https://wiki.dragino.com/docs/NB-IoT/rs485-sdi-12-sensor-nodes/ps-cb-na/>
- Wiki general CB/CS config: <https://wiki.dragino.com/docs/NB-IoT/general-configuration/cb-cs-models-nb-iot-lte-m/>

Raw dump with all format strings: [`STRING_LIBRARY.md`](STRING_LIBRARY.md).  
Regenerate that dump with `python3 PS-CB-NA/scripts/extract_string_library.py`.

Convention: serial debug lines are shown **without** the `[uptime_ms]` prefix.
So a log of `2026-08-16T19:36:50.344Z [67808]DNS configuration is successful`
is listed here as `DNS configuration is successful`.

---

## 1. AT command syntax

```
AT+<CMD>?        : Help on <CMD>
AT+<CMD>         : Run <CMD>
AT+<CMD>=<value> : Set the value
AT+<CMD>=?       : Get the value
```

Unlock: type the 6-digit box password (same as `AT+PIN=xxxxxx` on the label).  
Change password: `AT+PWORD=xxxxxx` (6 lowercase letters/digits).  
Many settings reply `Attention:Take effect after ATZ` and need `ATZ`.

### 1.1 Fixed AT replies

```
OK
ERROR
NULL
Attention:Take effect after ATZ
NOTE:IP format error
NOTE:SERVADDR FORMAT ERROR
Set after calibration time or take effect after ATZ
value needs to be greater than 1704067200
AT_PARAM_ERROR
AT_RX_ERROR
AT_BUSY_ERROR
AT_TEST_PARAM_OVERFLOW
The device is busy.
Password Correct
Password Incorrect
Password timeout
```

Bootloader extras: `AT_ERROR`, `AT_PARAM_OVERFLOW`, `AT_NO_NETWORK_JOINED`,
`AT_INVALID_MODE`, `AT_PAYLOAD_SIZE_ERROR`, `password error`, `boot error`,
`app error`, `error unknown`.

---

## 2. AT+PRO — protocol × payload type

`AT+PRO=<protocol>,<payload>`

| Protocol | Meaning |
| --- | --- |
| 1 | CoAP |
| 2 | UDP |
| 3 | MQTT |
| 4 | TCP |

| Payload | Meaning |
| --- | --- |
| 0 | HEX |
| 1 | ThingSpeak |
| 3 | ThingsBoard |
| 5 | General JSON |

Wiki examples:

```
AT+PRO=2,0   UDP + HEX
AT+PRO=2,5   UDP + JSON
AT+PRO=3,0   MQTT + HEX
AT+PRO=3,1   MQTT + ThingSpeak
AT+PRO=3,3   MQTT + ThingsBoard
AT+PRO=3,5   MQTT + JSON
AT+PRO=4,0   TCP + HEX
AT+PRO=4,5   TCP + JSON
```

Serial banner after boot: `Protocol in Used: MQTT` / `UDP` / `TCP` / `COAP`.

---

## 3. AT command catalogue

Columns: **Wiki** = listed on the PS-CB-NA wiki page; **FW** = present in v1.2.1
string table. Commands marked *wiki-only* may be outdated for this build.

### 3.1 General

| Command | Description | Wiki | FW |
| --- | --- | --- | --- |
| `AT` | Attention | ✓ | ✓ |
| `AT?` | Short help | ✓ | ✓ |
| `ATZ` | Reset MCU | ✓ | ✓ |
| `AT+MODEL` | Module / firmware info | ✓ | ✓ |
| `AT+DEUI` | Device ID (IMEI) | ✓ | ✓ |
| `AT+SLEEP` | Sleep status | ✓ | ✓ |
| `AT+DEBUG` | Extra serial debug | ✓ | ✓ |
| `AT+CFG` | Dump all settings | ✓ | ✓ |
| `AT+SERVADDR` | Server `host,port` | ✓ | ✓ |
| `AT+TDC` | Uplink interval (seconds) | ✓ | ✓ |
| `AT+INTMOD` | Interrupt mode 0..3 | ✓ | ✓ |
| `AT+APN` | Cellular APN | ✓ | ✓ |
| `AT+3V3T` | 3V3 power-on duration (ms) | ✓ | ✓ |
| `AT+5VT` | 5V power-on duration (ms) | ✓ | ✓ |
| `AT+12VT` | 12V power-on duration (ms) | ✓ | ✓ |
| `AT+PROBE` | Probe model `aabb` | ✓ | ✓ |
| `AT+PRO` | Protocol + payload type | ✓ | ✓ |
| `AT+RXDL` | RX / downlink wait window | ✓ | ✓ |
| `AT+GETSENSORVALUE` | Read sensors now | ✓ | ✓ |
| `AT+DNSCFG` | DNS servers | ✓ | ✓ |
| `AT+CSQTIME` | Network-join wait time | ✓ | ✓ |
| `AT+GDNS` | Enable/disable DNS | ✓ | ✓ |
| `AT+TLSMOD` | TLS mode | ✓ | ✓ |
| `AT+IPTYPE` | IPv4 / IPv6 | ✓ | ✓ |
| `AT+QSW` | Power BG95 on/off | ✓ | ✓ |
| `AT+CLOCKLOG` | Clock logging `a,b,c,d` | ✓ | ✓ |
| `AT+TIMESTAMP` | UNIX timestamp (s) | ✓ | ✓ |
| `AT+GETLOG` | Print serial logs | ✓ | ✓ |
| `AT+PWORD` | System password | ✓ | ✓ |
| `AT+FDR` | Factory reset (all) | ✓ | ✓ |
| `AT+FDR1` | Factory reset (keep passwords) | ✓ | ✓ |
| `AT+CDP` | Read/clear cached data | ✓ | ✓ |
| `AT+LDATA` | Last upload data | ✓ | ✓ |
| `AT+DOWNTE` | 1T vs GE downlink + DL debug | ✓ | ✓ |
| `AT+ROC` | Report-on-change / threshold alarm | ✓ | ✓ |
| `AT+STDC` | Multi-sample then one uplink | ✓ | ✗ wiki-only / not in v1.2.1 strings |

### 3.2 MQTT

| Command | Description | Wiki | FW |
| --- | --- | --- | --- |
| `AT+CLIENT` | MQTT client ID | ✓ | ✓ |
| `AT+UNAME` | MQTT username | ✓ | ✓ |
| `AT+PWD` | MQTT password | ✓ | ✓ |
| `AT+PUBTOPIC` | Publish topic | ✓ | ✓ |
| `AT+SUBTOPIC` | Subscribe topic | ✓ | ✓ |
| `AT+MQOS` | QoS 0 / 1 / 2 | ✓ | ✓ |

### 3.3 CoAP

| Command | Description | Wiki | FW |
| --- | --- | --- | --- |
| `AT+URI1` … `AT+URI5` | CoAP options | ✓ | ✓ |
| `AT+URI6` … `AT+URI8` | CoAP options | ✓ | ✗ removed (changelog v1.1.4) |

### 3.4 GPS / GNSS

| Command | Description | Wiki | FW |
| --- | --- | --- | --- |
| `AT+GPS` | GPS on/off (`0` default) | ✓ | ✓ |
| `AT+GNSST` | GNSS fix timeout (s) | ✓ | ✓ |
| `AT+GTDC` | GPS interval (hours) | ✓ | ✓ |

### 3.5 Firmware-only / general-config wiki (not on product AT list)

Present in v1.2.1, documented on the CB general-config wiki or only in firmware help:

| Command | Description |
| --- | --- |
| `AT+BKDNS` | Backup / dynamic DNS IP for domain resolution |
| `AT+IOTMOD` | LTE search category: `0` eMTC, `1` NB-IoT, `2` both |
| `AT+QBAND` | Frequency band bitmasks `<eMTC>,<NB-IoT>` |
| `AT+QCOPS` | Operator code (COPS) |
| `AT+NTP` | NTP server |
| `AT+SNI` | Server Name Indication / domain-direct connect |
| `AT+REDPT` | Retransmit on missing ACK |
| `AT+CERTMOD` | Enter TLS certificate write mode |
| `AT+UPGRADE` | OTA upgrade |
| `AT+OTASER` | OTA MQTT server |
| `AT+OTACLT` | OTA MQTT client ID |
| `AT+OTAUNAME` | OTA MQTT username |
| `AT+OTAPWD` | OTA MQTT password |
| `AT+OTATITLE` | OTA firmware title |
| `AT+OTAVER` | OTA firmware version |
| `AT+PLDTA` / `AT+PDTA` / `AT+CLRDTA` | Print / clear stored samples |
| `AT+NAME…` | BLE advertised name (often `AT+NAME` + IMEI) |
| `AT+PWRM2` | Bootloader / power-management handshake |

---

## 4. Serial debug / status lines

These are the human-readable strings the application firmware prints on UART
(with a `[%u]` uptime prefix unless noted). Grouped by stage of an upload cycle.

### 4.1 Boot and module power

```
DRAGINO PS-CB SensorManual
DRAGINO NB bootloader v1.3
Image Version: v1.2.1
NB-IoT Stack : D-BG95-004
NB module is initializing...
NBIOT did not respond.
NBIOT has responded.
Echo mode turned off failed.
Echo mode turned off successfully.
Successfully awakened module
Closing NB module...
NB module power-off successful.
No response when shutting down
Restart the module...
power on
power off
Hardware Not Support
The device is busy.
Please wait for the erase to complete
PS-CB sensor detected
Enter Debug mode
Exit Debug mode
```

### 4.2 Identity and radio

```
Request for manufacturer model failed.
Model information:BG95-M1.
Model information:BG95-M2.
Model information:BG95-M3.
Failed to read IMEI number.
The IMEI number is:%s.
Failed to read IMSI number.
The IMSI number is:%s.
Failed to set the Frequency band.
Frequency band configuration successful.
Failed to set the Network Category.
Configure Network Category to be Searched for under LTE RAT.
Failed to set COPS
Signal Strength:%d
Signal Strength:%d *%d
Signal Strength:99 *%d
Network Information:%s
Unable to query network information
Turn off the module receiving and sending RF function.
Failed to get time
```

Example observed: `Network Information:"eMTC","27402","LTE BAND 20",6300`

### 4.3 APN / PDP / data format

```
Failed to set APN
Set APN successfully
Failed to set the data format.
Set the data format for sending and receiving.
Failed to configure parameters for TCP/IP context
Successfully configured parameters for TCP/IP context
Failed to deactivate PDP context
Successfully deactivated PDP context
Failed to activate PDP context
Successfully activated PDP context
```

### 4.4 DNS / NTP

```
DNS configuration failed
DNS configuration is successful
No DNS resolution required
Resolving domain name...
Domain IP:%s
Domain name resolution failed
Domain name resolution failed*%d
NTP configuration failed
NTP configuration is successful
```

### 4.5 TLS / certificates

```
Failed to configure authentication mode
Manage server and client authentication.
Failed to configure server Name Indication feature
Enable Server Name Indication feature.
Failed to enable SSL mode
Enable SSL and configure SSL context/connect index.
Failed to configure CA certificate
Configure the path of CA certificate for SSL.
Failed to configure client certificate
Configure the path of client certificate for SSL.
Failed to configure client private key
Configure the path of client private key for SSL.
Enter certificate mode
Exit certificate mode
```

### 4.6 MQTT path

```
MQTT parameter configuration error
MQTT configuration failed
Failed to open the MQTT client network
Opened the MQTT client network successfully
Failed to connect to server
Successfully connected to the server
Failed to subscribe to topic
Subscribe to topic successfully
Failed to Set PUB
Failed to disconnect client from MQTT server
Successfully disconnected the client from the MQTT server
Failed to close TCP connection
The TCP connection is closed successfully
MQTT open success
MQTT connect success
uploading 100%,the device will disconnect MQTT server and power off NB module
```

Typical failure sequence seen in logs when CONNECT never completes:

```
Opened the MQTT client network successfully
Failed to send
*****End of upload*****
```

(`Successfully connected to the server` / `Failed to connect to server` never
appear in current captures — CONNECT outcome is silent.)

### 4.7 UDP / TCP socket path

```
UDP parameter configuration error
Datagram is sent by RF
TCP parameter configuration error
Failed to open a Socket Service
Open a Socket Service successfully
Failed to upload data
Failed to close the port
Close the port successfully
SEND FAIL
```

### 4.8 CoAP path

```
COAP parameter configuration error
COAP configuration failed
COAP configuration successfully
Failed to Create a CoAP session
Failed to Set a CoAP message ID
Set the CoAP message ID to 1234 and automatically generate a token
Failed to configure the CoAP option index 1
Successfully configured CoAP option index 1
Failed to configure the CoAP option index 2
Successfully configured CoAP option index 2
Failed to configure the CoAP option index 3
Successfully configured CoAP option index 3
Failed to configure the CoAP option index 4
Successfully configured CoAP option index 4
Failed to configure the CoAP option index 5
Successfully configured CoAP option index 5
Failed to close CoAP session
Closed the CoAP session successfully
Create a CoAP session and connect to the CoAP server
```

### 4.9 Upload cycle markers

```
Protocol in Used:
*****Upload start:%u*****
*****End of upload*****
Upload data successfully
Send complete
Failed to send
```

### 4.10 Sensor printout (during upload)

```
BAT:%0.3f V
Battery: %.3f V
IN1:%d
IN2:%d
GPIO_EXTI:%d
IDC_Input:%0.3f mA
VDC_Input:%0.3f V
IDC : %0.3f mA;VDC: %0.3f V
latitude:%f,longitude:%f
water_deep:%.3f
pressure:%.3f
differential_pressure::%.3f
```

### 4.11 GNSS

```
GNSS failed to turn on or GNSS is running
Successfully turn on GNSS,Fix Timeout:%ds
NB module is obtaining location information...
Searching for location...
Successfully obtained location information
Failed to obtain location information
Failed to turn off GNSS
Successfully turn off GNSS
```

### 4.12 Downlink

```
Received downlink data:%s
Debug downlink data:%s
Retrieve the Received downlink Data
Retrieve received data failed
send retrieve data completed
No data retrieved
No data in buffer
Confirm ACK
Event:Status
Reset the device after receiving the downlink...
Downstream parameter error
Clear all stored sensor data...
Stop Tx events when read sensor data
```

JSON downlink wrapper (MQTT / TCP / UDP):

```
{"Config":"[AT+…;ATZ]"}
{"IMEI":"%s","Downklink_Ack":"success"}
```

(`Downklink` spelling is in firmware.)

### 4.13 OTA

```
OTA parameter configuration error
Failed to open the OTA client network
Failed to connect to ota server
Failed to Set firmware information
Failed to Set request firmware information
Failed to Set request download
Failed to Set ota update information
Upload firmware information successfully
Upload request firmware information successfully
Request download successfully
Downloading %d%
Close OTA upgrade
Consistent firmware
Inconsistent firmware title
Inconsistent firmware version
server_fw_title %s
server_fw_version %s
server_fw_size %d
CRC error %08X %08X
```

### 4.14 Flash / reboot cause

```
error in Erase operation
error in Flash Erase operation
error in Write operation
error in Flash Write operation
write_error_flag:%x
reboot error:Low-power!
reboot error:Window watchdog!
reboot error:Independent watchdog!
reboot error:Software!
reboot error:POR/PDR!
reboot error:NRST!
reboot error:BOR!
```

---

## 5. Payload field dictionary (JSON type=5)

Field names as emitted in general JSON uplinks (wiki + firmware templates):

| Field | Meaning |
| --- | --- |
| `IMEI` | Modem IMEI |
| `IMSI` | SIM IMSI (since FW ≥ 1.1.0) |
| `Model` | Product model string (`PS-CB`) |
| `idc_input` | 0–20 mA input |
| `vdc_input` | 0–30 V input |
| `probe_model` | `AT+PROBE` value (`%04x`) |
| `water_deep_cm` / `pressure_kPa` / `differential_pressure_Pa` | Converted probe values |
| `interrupt` | Interrupt flag |
| `interrupt_level` | GPIO_EXTI level |
| `battery` | Battery volts |
| `signal` | CSQ 0–31 or 99 |
| `idc_alarm` / `vdc_alarm` | ROC alarm enum (see below) |
| `time` | ISO8601 sample time |
| `latitude` / `longitude` | GNSS |
| `gps_time` | GNSS fix time (`1970-01-01…` if GPS off/fail) |
| `"1"` … `"8"` | Clock-log history entries `[idc, vdc, time]` |

### 5.1 Alarm enums (`AT+ROC`)

```
NULL
IDC_INC
IDC_DEC
IDC_LOW
IDC_HIGH
VDC_INC
VDC_DEC
VDC_LOW
VDC_HIGH
```

HEX payload ROC codes: `0x00` normal, `0x01` incremental, `0x02` decremental.

### 5.2 CSQ mapping (wiki)

| CSQ | Meaning |
| --- | --- |
| 0 | ≤ −113 dBm |
| 1 | −111 dBm |
| 2…30 | −109 … −53 dBm |
| 31 | ≥ −51 dBm |
| 99 | unknown / not detectable |

---

## 6. HEX payload field names (type=0)

Order / labels used in docs and debug:

```
Device ID (f+IMEI)
SIM Card ID (f+IMSI)
Version (model + FW)
BAT
Signal Strength
IN1
IN2
GPIO_EXTI Level
GPIO_EXTI Flag
idc_alarm
vdc_alarm
Probe Model
Latitude
Longitude
GPS_Timestamp
0~20mA
0~30V
TimeStamp
```

Hardware model byte for PS-CB-NA: `0x46`.

---

## 7. Common downlink command codes (wiki)

| Code | Maps to |
| --- | --- |
| `0x01` | `AT+TDC` |
| `0x03` | `AT+CLOCKLOG` |
| `0x06` | `AT+INTMOD` |
| `0x07` | `AT+3V3T` / `AT+5VT` / `AT+12VT` |
| `0x08` | `AT+PROBE` |
| `0x09` | `AT+ROC` |
| `0xAE` | `AT+STDC` (wiki; may be absent on v1.2.1) |

MQTT JSON equivalent:

```
{"Config":"[AT+TDC=90;ATZ]"}
{"Config":"[AT+SERVADDR=host,port;AT+PRO=3,5;ATZ]"}
```

Special downlink: `Event:Status` → device replies with version/info packet.

---

## 8. BG95 modem AT strings the app issues

Not user-facing, but useful when sniffing modem UART / decoding firmware flow:

```
ATE0
AT+CFUN=%d
AT+CGATT?
AT+CGDCONT=1,"IPV4V6","%s"
AT+CGMM
AT+CGSN
AT+CIMI
AT+COPS=1,2,"
AT+CSQ
AT+CCLK?
AT+QCFG="iotopmode",%d,1
AT+QCFG="band",0xF,%s,1
AT+QNWINFO
AT+QICSGP=1,%d,"","","",1
AT+QICSGP=1,%d,"%s","","",1
AT+QIACT=1
AT+QIDEACT=1
AT+QICFG="dataformat",0,0
AT+QICFG="dataformat",1,0
AT+QIDNSCFG=1,
AT+QIDNSGIP=1,
AT+QNTP=1,
AT+QMTOPEN=0,"
AT+QMTCONN=0,"
AT+QMTCFG="version",0,4
AT+QMTCFG="ssl",0,1,0
AT+QMTPUB=0,…
AT+QMTSUB=0,1,"
AT+QMTDISC=0
AT+QMTCLOSE=0
AT+QIOPEN=1,0,"TCP","
AT+QIOPEN=1,0,"UDP","
AT+QIOPEN=1,0,"UDP SERVICE","
AT+QISEND=
AT+QISENDEX=
AT+QIRD=0
AT+QIRD=0,1500
AT+QICLOSE=0
AT+QSSLCFG="cacert",0,"cacert.pem"
AT+QSSLCFG="clientcert",0,"client.pem"
AT+QSSLCFG="clientkey",0,"user_key.pem"
AT+QSSLCFG="seclevel",0,
AT+QSSLCFG="sni",0,
AT+QCOAPOPEN=0,"
AT+QCOAPCFG="pdpcid",0,1
AT+QCOAPHEADER=0,1234,1
AT+QCOAPOPTION=0,0,…
AT+QCOAPSEND=0,1,2,255
AT+QCOAPCLOSE=0
AT+QGPS
AT+QGPS=1
AT+QGPSLOC=2,0
AT+QGPSEND
```

---

## 9. Wiki vs firmware notes (v1.2.1)

- Wiki still lists `AT+URI6`…`AT+URI8`; changelog v1.1.4 removed three URI
  entries to free flash — only `URI1`–`URI5` remain in this binary.
- Wiki documents `AT+STDC`; that string is **not** in the v1.2.1 image.
- Firmware adds `AT+BKDNS`, `AT+IOTMOD`, `AT+QBAND`, `AT+NTP`, `AT+SNI`,
  `AT+REDPT`, full OTA set, `AT+CERTMOD` — see general CB config wiki.
- Default MQTT QoS in changelog v1.2.0 is `0`; set `AT+MQOS=1` if you need PUBACK.
- Label password unlock text is `AT+PIN=xxxxxx`; UART unlock is just typing the
  six digits (firmware replies `Password Correct` / `Password Incorrect` /
  `Password timeout`).

---

## 10. Quick grep patterns for log mining

```
Successfully configured parameters for TCP/IP context
Successfully deactivated PDP context
Successfully activated PDP context
DNS configuration is successful
No DNS resolution required
Opened the MQTT client network successfully
Successfully connected to the server
Failed to open the MQTT client network
MQTT configuration failed
MQTT parameter configuration error
Failed to send
Upload data successfully
*****Upload start:
*****End of upload*****
NBIOT has responded.
NB module power-off successful.
Password Correct
Attention:Take effect after ATZ
Protocol in Used:
```
