# PS-CB-NA string library

Every log / debug / response string the sensor can emit, extracted from the
firmware image string table and cross-referenced against the captured serial
logs in `logs/`.

- Source firmware: `PS-CB-NA/firmware/PS-CB-NA_v1.2.1.bin` (Image Version v1.2.1, NB-IoT stack D-BG95-004)
- Source bootloader: `PS-CB-NA/firmware/DRAGINO_NB_bootloader_v1.3.bin`
- Log files scanned: 45
- Firmware strings recovered: 511 (57 confirmed in logs)

`%u` is the millisecond uptime the firmware prints as `[%u]` at the start of a
line. `%s` is a string, `%d` an integer, `%.3f` a float. Regenerate this file
with `python3 PS-CB-NA/scripts/extract_string_library.py`.

A leading `*` marks a string confirmed in the captured logs.

## JSON uplink and status payloads

```
  {"current_fw_title": "%s", "current_fw_version": "%s"}
  {"IMEI":"%s", "Message": "Firmware Title Mismatch"}
  {"IMEI":"%s", "Message": "Hardware Not Support"}
  {"IMEI":"%s", "Message": "Error Format : command incorrect"}
  {"IMEI":"%s", "Message": "OTA server connecting timeout"}
  {"IMEI":"%s", "Message": "Firmware Title not found"}
  {"IMEI":"%s", "Message": "Consistent firmware Version"}
  {"IMEI":"%s", "Message": "Firmware CheckSum Error"}
  {"IMEI":"%s", "Log": "%s"}
  {"IMEI":"%s","Downklink_Ack":"success"}
  {"Config":"[
  {"IMEI":"%s","IMSI":"%s","Message_Type":"Datalog",
  {"IMEI":"%s","IMSI":"%s","Model":"%s","idc_input":%.3f,"vdc_input":%.3f,"probe_model":"%04x","differential_pressure_Pa":%.2f,"interrupt":%d,"interrupt_level":%d,"battery":%.3f,"signal":%d,"idc_alarm":"%s","vdc_alarm":"%s","time":"%d-%02d-%02dT%02d:%02d:%02dZ","latitude":%f,"longitude":%f,"gps_time":"%d-%02d-%02dT%02d:%02d:%02dZ"
  {"IMEI":"%s","IMSI":"%s","Model":"%s","idc_input":%.3f,"vdc_input":%.3f,"probe_model":"%04x","pressure_kPa":%.2f,"interrupt":%d,"interrupt_level":%d,"battery":%.3f,"signal":%d,"idc_alarm":"%s","vdc_alarm":"%s","time":"%d-%02d-%02dT%02d:%02d:%02dZ","latitude":%f,"longitude":%f,"gps_time":"%d-%02d-%02dT%02d:%02d:%02dZ"
  {"IMEI":"%s","IMSI":"%s","Model":"%s","idc_input":%.3f,"vdc_input":%.3f,"probe_model":"%04x","water_deep_cm":%.2f,"interrupt":%d,"interrupt_level":%d,"battery":%.3f,"signal":%d,"idc_alarm":"%s","vdc_alarm":"%s","time":"%d-%02d-%02dT%02d:%02d:%02dZ","latitude":%f,"longitude":%f,"gps_time":"%d-%02d-%02dT%02d:%02d:%02dZ"
  {"IMEI":"%s","IMSI":"%s","Model":"%s","idc_input":%.3f,"vdc_input":%.3f,"interrupt":%d,"interrupt_level":%d,"battery":%.3f,"signal":%d,"idc_alarm":"%s","vdc_alarm":"%s","time":"%d-%02d-%02dT%02d:%02d:%02dZ","latitude":%f,"longitude":%f,"gps_time":"%d-%02d-%02dT%02d:%02d:%02dZ"
  {"current_fw_title": "%s", "current_fw_version": "%s", "fw_state": "UPDATED"}
  {"current_fw_title": "%s", "current_fw_version": "%s", "fw_state": "DOWNLOADING"}
  {"sharedKeys": "fw_checksum,fw_checksum_algorithm,fw_size,fw_title,fw_version"}
  {"IMEI":"%s", "Message": "OTA Update OK, Firmware Title: %s Ver: %s"}
  {"IMEI":"%s","Image Version":"%s","NB-IoT Stack":"%s","Model":"%s"}
```

## Downlink JSON config keys

```
  AT+SERVADDR":"
  AT+CLIENT":"
  AT+UNAME":"
  AT+PWD":"
  AT+PUBTOPIC":"
  AT+SUBTOPIC":"
  AT+TDC":"
  AT+APN":"
  AT+PRO":"
  AT+CSQTIME":"
  AT+BKDNS":"
  AT+GDNS":"
  AT+TLSMOD":"
  AT+MQOS":"
  AT+IPTYPE":"
  AT+GTDC":"
  AT+GNSST":"
  AT+GPS":"
```

## Boot, module power and reset

```
  [%u]NBIOT did not respond.
* [%u]NBIOT has responded.
  [%u]Echo mode turned off failed.
* [%u]Echo mode turned off successfully.
* [%u]Closing NB module...
* [%u]NB module power-off successful.
  [%u]Restart the module...
  [%u]No response when shutting down
  NB module power-off successful.
* [%u]NB module is initializing...
  Hardware Not Support
  Successfully awakened module
  Please wait for the erase to complete
  The device is busy.
  [%u]power on
  [%u]power off
* AT+PWRM2
  AT+CFUN=%d
  [%u]reboot error:Low-power!
  [%u]reboot error:Window watchdog!
  [%u]reboot error:Independent watchdog!
  [%u]reboot error:Software!
  [%u]reboot error:POR/PDR!
  [%u]reboot error:NRST!
  [%u]reboot error:BOR!
  AT+QSW
  AT+QSW  : Power on and power off BG95 module
```

## Modem identity and radio

```
  [%u]Request for manufacturer model failed.
  [%u]Model information:BG95-M1.
* [%u]Model information:BG95-M2.
  [%u]Model information:BG95-M3.
  [%u]Failed to read IMEI number.
* [%u]The IMEI number is:%s.
  [%u]Failed to read IMSI number.
* [%u]The IMSI number is:%s.
  [%u]Failed to set the Frequency band.
* [%u]Frequency band configuration successful.
  [%u]Failed to set the Network Category.
  Signal Strength:%d *%d
  [%u]Turn off the module receiving and sending RF function.
  [%u]Failed to set COPS
* [%u]Signal Strength:%d
  Signal Strength:99 *%d
  [%u]Unable to query network information
  AT+QCOPS=
  AT+CSQTIME=
  AT+QBAND=
* AT+CSQ
  AT+CGATT?
  AT+CGMM
  AT+CGSN
  AT+CIMI
  AT+COPS=1,2,"
  AT+QCFG="iotopmode",%d,1
  AT+QCFG="band",0xF,%s,1
* [%u]Network Information:%s
  AT+QNWINFO
  AT+CSQTIME  : Get or Set the time to join the network
  AT+QBAND
  AT+CSQTIME
  AT+QCOPS
  AT+IOTMOD: Configure Network Category to be Searched for under LTE RAT
  AT+QBAND: Get or set Frequency Band
  AT+QCOPS: Get or set operator code
* [%u]Configure Network Category to be Searched for under LTE RAT.
```

## Time and clock

```
* [%u]Failed to get time
  AT+CLOCKLOG=
  Set after calibration time or take effect after ATZ
  AT+TIMESTAMP=
  evalue needs to be greater than 1704067200
  [%u]CLOCK:
  AT+CCLK?
  AT+CLOCKLOG
  AT+TIMESTAMP
  AT+TIMESTAMP  : Get or Set UNIX timestamp in second
  AT+CLOCKLOG: Enable or Disable Clock Logging
```

## APN, PDP context and data format

```
  [%u]Failed to set the data format.
* [%u]Set the data format for sending and receiving.
  [%u]Failed to set APN
* [%u]Set APN successfully
  [%u]Failed to configure parameters for TCP/IP context
* [%u]Successfully configured parameters for TCP/IP context
  [%u]Failed to deactivate PDP context
* [%u]Successfully deactivated PDP context
* [%u]Failed to activate PDP context
* [%u]Successfully activated PDP context
* AT+APN=
  AT+CGDCONT=1,"IPV4V6","%s"
  AT+QIACT=1
  AT+QICFG="dataformat",0,0
  AT+QICFG="dataformat",1,0
  AT+QICSGP=1,%d,"","","",1
  AT+QICSGP=1,%d,"%s","","",1
  AT+QIDEACT=1
  AT+APN     : Get or set the APN
  AT+APN
```

## DNS and NTP

```
* [%u]DNS configuration failed
* [%u]DNS configuration is successful
  [%u]NTP configuration failed
  [%u]NTP configuration is successful
* [%u]No DNS resolution required
  [%u]Resolving domain name...
  [%u]Domain IP:%s
  AT+DNSCFG=
  AT+BKDNS=
  AT+GDNS=
  AT+NTP=
  Domain name resolution failed*%d
  [%u]Domain name resolution failed
  AT+QIDNSGIP=1,
  AT+QIDNSCFG=1,
  AT+QNTP=1,
  AT+BKDNS  : Get or Set dynamic domain name resolution IP
  AT+GDNS  : Get or Set the DNS
  AT+DNSCFG
  AT+NTP
  AT+GDNS
  AT+BKDNS
  AT+NTP: Get or set NTP Server
  AT+DNSCFG  : Get or Set DNS Server
```

## TLS / SSL / certificates

```
  [%u]Failed to configure authentication mode
  [%u]Manage server and client authentication.
  [%u]Failed to configure server Name Indication feature
  [%u]Enable Server Name Indication feature.
  [%u]Failed to enable SSL mode
  [%u]Enable SSL and configure SSL context/connect index.
  [%u]Failed to configure CA certificate
  [%u]Configure the path of CA certificate for SSL.
  [%u]Failed to configure client certificate
  [%u]Configure the path of client certificate for SSL.
  [%u]Failed to configure client private key
  [%u]Configure the path of client private key for SSL.
  [%u]Enter certificate mode
  [%u]Exit certificate mode
  AT+QSSLCFG="cacert",0,"cacert.pem"
  AT+QSSLCFG="clientcert",0,"client.pem"
  AT+QSSLCFG="clientkey",0,"user_key.pem"
  AT+QSSLCFG="seclevel",0,
  AT+QSSLCFG="sni",0,
  AT+CERTMOD: Enter certificate mode
  AT+SNI  : Enable or disable Server Name Indication feature
```

## MQTT

```
* [%u]MQTT parameter configuration error
* [%u]MQTT configuration failed
* [%u]Failed to open the MQTT client network
  [%u]Failed to connect to server
  [%u]Failed to subscribe to topic
  [%u]Failed to Set PUB
  [%u]Failed to disconnect client from MQTT server
* [%u]Failed to close TCP connection
* [%u]The TCP connection is closed successfully
* [%u]Opened the MQTT client network successfully
  [%u]Successfully connected to the server
  [%u]Subscribe to topic successfully
  [%u]Successfully disconnected the client from the MQTT server
  MQTT open success
  MQTT connect success
  AT+QMTCLOSE=0
  AT+QMTCFG="version",0,4
  AT+QMTCONN=0,"
  AT+QMTDISC=0
  AT+QMTOPEN=0,"
  AT+QMTPUB=0,1,2,0,"channels/
  AT+QMTPUB=0,0,0,0,"channels/
  AT+QMTPUB=0,1,1,0,"channels/
  AT+QMTPUB=0,1,2,0,"
  AT+QMTPUB=0,0,0,0,"
  AT+QMTPUB=0,1,1,0,"
  AT+QMTSUB=0,1,"
  tb/mqtt-integration-tutorial/sensors/
  AT+QMTCFG="ssl",0,1,0
  v1/devices/me/telemetry
  v1/devices/me/attributes/request/1
  AT+PRO     : Get or Set usage agreement (1:COAP,2:UDP,3:MQTT,4:TCP)
  AT+OTACLT  : Get or Set the OTA MQTT clientID
  AT+CLIENT  : Get or Set the MQTT clientID
  AT+MQOS  : Set the QoS level of MQTT
  AT+PUBTOPIC: Get or set MQTT publishing topic
  AT+SUBTOPIC: Get or set MQTT subscription topic
  AT+OTAPWD     : Get or Set the OTA MQTT password
  AT+PWD     : Get or Set the MQTT password
  AT+OTAUNAME   : Get or Set the OTA MQTT Username
  AT+UNAME   : Get or Set the MQTT Username
  uploading 100%%,the device will disconnect MQTT server and power off NB module
```

## UDP

```
* [%u]UDP parameter configuration error
  [%u]Datagram is sent by RF
  AT+QIOPEN=1,0,"UDP SERVICE","
```

## TCP / socket service

```
  [%u]TCP parameter configuration error
* [%u]Failed to open a Socket Service
  [%u]Open a Socket Service successfully
  [%u]Failed to upload data
  [%u]Failed to close the port
* [%u]Close the port successfully
  AT+QICLOSE=0
  AT+QIOPEN=1,0,"TCP","
  AT+QIRD=0,1500
  AT+QISEND=
  AT+QISENDEX=
  SEND FAIL
  AT+QIOPEN=1,0,"UDP","
  AT+QIRD=0
```

## CoAP

```
  [%u]COAP parameter configuration error
  [%u]COAP configuration failed
  [%u]COAP configuration successfully
  [%u]Failed to Create a CoAP session
  [%u]Failed to Set a CoAP message ID
  [%u]Failed to configure the CoAP option index 1
  [%u]Successfully configured CoAP option index 1
  [%u]Failed to configure the CoAP option index 2
  [%u]Successfully configured CoAP option index 2
  [%u]Failed to configure the CoAP option index 3
  [%u]Successfully configured CoAP option index 3
  [%u]Failed to configure the CoAP option index 4
  [%u]Successfully configured CoAP option index 4
  [%u]Failed to configure the CoAP option index 5
  [%u]Successfully configured CoAP option index 5
  [%u]Failed to close CoAP session
  [%u]Closed the CoAP session successfully
  [%u]Create a CoAP session and connect to the CoAP server
  AT+URI1=
  AT+URI2=
  AT+URI3=
  AT+URI4=
  AT+URI5=
  AT+QCOAPCLOSE=0
  AT+QCOAPCFG="pdpcid",0,1
  AT+QCOAPHEADER=0,1234,1
  AT+QCOAPOPEN=0,"
  AT+QCOAPOPTION=0,0,0,
  AT+QCOAPOPTION=0,0,1,
  AT+QCOAPOPTION=0,0,2,
  AT+QCOAPOPTION=0,0,3,
  AT+QCOAPOPTION=0,0,4,
  AT+QCOAPSEND=0,1,2,255
  AT+URI1: Get or set CoAP option 1
  AT+URI1
  AT+URI2: Get or set CoAP option 2
  AT+URI2
  AT+URI3: Get or set CoAP option 3
  AT+URI3
  AT+URI4: Get or set CoAP option 4
  AT+URI4
  AT+URI5: Get or set CoAP option 5
  AT+URI5
  [%u]Set the CoAP message ID to 1234 and automatically generate a token
```

## Upload cycle

```
* *****Upload start:%u*****
  [%u]*****Upload start:%u*****
* [%u]Upload data successfully
* [%u]*****End of upload*****
  [%u]Send complete
* [%u]Failed to send
  Protocol in Used:
```

## Sensor readings and payload fields

```
  PS-CB sensor detected
* [%u]Battery: %.3f V
* [%u]IDC : %0.3f mA;VDC: %0.3f V
  IDC_INC
  IDC_DEC
  VDC_INC
  VDC_DEC
  IDC_LOW
  IDC_HIGH
  VDC_LOW
  VDC_HIGH
  @%d/%d/%d %02d:%02d:%02d idc_input=%.3f vdc_input=%.3f
  water_deep:%.3f
  pressure:%.3f
  differential_pressure::%.3f
* [%u]BAT:%0.3f V
* [%u]IN1:%d
* [%u]IN2:%d
* [%u]GPIO_EXTI:%d
* [%u]IDC_Input:%0.3f mA
* [%u]VDC_Input:%0.3f V
  field1=%.3f&field2=%.3f&field3=%.3f&field4=%d&field5=%f&field6=%f
```

## GNSS / GPS

```
  [%u]GNSS failed to turn on or GNSS is running
  [%u]Successfully turn on GNSS,Fix Timeout:%ds
  [%u]NB module is obtaining location information...
  [%u]Searching for location...
  [%u]Successfully obtained location information
  [%u]Failed to obtain location information
  [%u]Failed to turn off GNSS
  [%u]Successfully turn off GNSS
  AT+GNSST=
  AT+QGPS
  AT+QGPS=1
  [%u]latitude:%f,longitude:%f
  latitude:%f,longitude:%f
  AT+QGPSLOC=2,0
  AT+QGPSEND
  AT+GNSST  : Extend the time to turn on GNSS
  AT+GNSST
```

## Downlink handling

```
  [%u]Retrieve received data failed
  [%u]Retrieve the Received downlink Data
  [%u]send retrieve data completed
  [%u]No data retrieved
  Clear all stored sensor data...
  Stop Tx events when read sensor data
  [%u]Debug downlink data:%s
  Event:Status
  [%u]Received downlink data:%s
  [%u]No data in buffer
  [%u]Confirm ACK
  [%u]Reset the device after receiving the downlink...
  Downstream parameter error
```

## OTA upgrade

```
  OTA parameter configuration error
  Failed to open the OTA client network
  Failed to connect to ota server
  Failed to Set firmware information
  Failed to Set request firmware information
  Failed to Set request download
  Failed to Set ota update information
  Close OTA upgrade
  Upload firmware information successfully
  Upload request firmware information successfully
  Request download successfully
  Downloading %d%%
  CRC error %08X %08X
  AT+OTACLT=
  AT+OTATITLE=
  AT+OTAVER=
  AT+OTAPWD=
  AT+OTASER=
  AT+OTAUNAME=
  v2/fw/request/1/chunk/%d
  Inconsistent firmware title
  Consistent firmware
  Inconsistent firmware version
  server_fw_title %s
  server_fw_version %s
  server_fw_size %d
  AT+UPGRADE  : OTA upgrade.
  AT+OTAPWD
  AT+OTATITLE
  AT+OTAUNAME
  AT+OTASER
  AT+OTAVER
  AT+OTACLT
  AT+OTATITLE: Get or set OTA firmware title
  AT+OTAVER: Get or set OTA firmware version
  AT+OTASER: Get or Set the OTA Server address
```

## Console password and access

```
* [%u]Password Correct
* [%u]Password Incorrect
  Exit Debug mode
  Enter Debug mode
  AT+PWORD=
* [%u]Password timeout
  AT+PWORD
  AT+PWORD   : Get or set the System password
```

## AT command layer: errors and notices

```
* NULL
  NOTE:IP format error
* Attention:Take effect after ATZ
  NOTE:SERVADDR FORMAT ERROR
  AT_PARAM_ERROR
  AT_RX_ERROR
  AT_BUSY_ERROR
  AT_TEST_PARAM_OVERFLOW
  AT+<CMD>?        : Help on <CMD>
  AT+<CMD>         : Run <CMD>
  AT+<CMD>=<value> : Set the value
  AT+<CMD>=?       : Get the value
```

## AT command setters (echoed on write)

```
  AT+12VT=
  AT+3V3T=
  AT+5VT=
  AT+CLIENT=
  AT+DEUI=
  AT+DOWNTE=
  AT+GETLOG=
  AT+GPS=
  AT+GTDC=
  AT+INTMOD=
  AT+IOTMOD=
  AT+IPTYPE=
  AT+LDATA=
  AT+MODEL=
  AT+MQOS=
  AT+PRO=
  AT+PROBE=
  AT+PUBTOPIC=
* AT+PWD=
  AT+REDPT=
  AT+ROC=
  AT+RXDL=
  AT+SERVADDR=
  AT+SLEEP=
  AT+SNI=
  AT+SUBTOPIC=
  AT+TDC=
  AT+TLSMOD=
* AT+UNAME=
```

## AT command help text

```
  ATAT+PLDTA: Print the last few sets of data
  ATAT+PDTA: Print the sector data from start page to stop page
  ATAT+CLRDTA: Clear the storage, record position back to 1st
  AT+TLSMOD  : Get or Set the TLS mode
  AT+INTMOD  : Get or Set the trigger interrupt mode (0:input,1:falling or rising,2:falling,3:rising)
  AT+IPTYPE  : Set the IPv4 or IPv6
  AT+DEUI    : Get or set the Device ID
  AT+GPS  : Turn off and on GPS
  ATZ        : Trig a reset of the MCU
  AT+LDATA   : Get the last upload data
  AT+CDP     : Read or Clear cached data
  AT+REDPT: Get or Set the function of reconnecting the network to send data
  AT+RXDL    : Get or Set the receiving time
  AT+GTDC     : Get or set GPS positioning interval in units of h
  AT+PROBE     : Get or Set the probe model
  AT+ROC  : Get or set threshold alarm
  AT+MODEL   : Get module information
  AT+3V3T     : Get or Set extend the time of 3V3 power
  AT+12VT     : Get or Set extend the time of 12V power
  AT+5VT     : Get or Set extend the time of 5V power
  AT+TDC     : Get or set the application data transmission interval in s
  AT+FDR1     : Reset parameters to factory default values except for passwords
  AT+CFG     : Print all settings
  AT+GETLOG  : Print serial port logs
  AT+DOWNTE: Get or set the conversion between the standard version and 1T version downlinks
  AT+SERVADDR: Get or Set the Server address
  AT+SLEEP    : Get or set the sleep status
  AT+FDR     : Reset Parameters to Factory Default
  AT+GETSENSORVALUE     : Returns the current sensor measurement
  AT+DEBUG    : Set more info output
```

## AT command names (AT+CFG / help index)

```
  ATE0
* ATZ
  AT+FDR1
  AT+LDATA
  AT+PLDTA
  AT+PDTA
  AT+CLRDTA
  AT+TDC
  AT+GTDC
  AT+PUBTOPIC
  AT+SUBTOPIC
  AT+ROC
  AT+TLSMOD
  AT+INTMOD
  AT+IOTMOD
  AT+CERTMOD
  AT+PWD
  AT+PROBE
  AT+UPGRADE
  AT+UNAME
  AT+IPTYPE
  AT+DOWNTE
  AT+GETSENSORVALUE
* AT+CFG
  AT+GETLOG
  AT+DEBUG
  AT+SNI
  AT+DEUI
  AT+RXDL
  AT+MODEL
  AT+PRO
  AT+CDP
  AT+SLEEP
  AT+SERVADDR
  AT+FDR
  AT+MQOS
  AT+GPS
  AT+3V3T
  AT+CLIENT
  AT+REDPT
  AT+12VT
  AT+5VT
```

## Flash and storage

```
  error in Erase operation
  error in Flash Erase operation
  error in Write operation
  write_error_flag:%x
  error in Flash Write operation
```

## Manual banner

```
* DRAGINO PS-CB SensorManual
```

## Generic primitives and bare format strings

```
  AT?
  [%u]%s
  +QMTRECV
  [%u]
  AT
* AT+NAME%s
```

## Bootloader (DRAGINO NB bootloader v1.3)

```
  pGBiJ@BapG
  OK
  AT+BAUD3
* AT+PWRM2
  AT+RESET
  @boot error
  AT+BAUD7
  app error
  password error
* DRAGINO NB bootloader v1.3
  AT+DATA=
  AT_NO_NETWORK_JOINED
  AT_INVALID_MODE
  AT_PAYLOAD_SIZE_ERROR
  AT_PARAM_ERROR
  AT_ERROR
  AT_RX_ERROR
  AT_BUSY_ERROR
  ATZ: Trig a reset of the MCU
  AT_PARAM_OVERFLOW
  AT+MOD: Get current image version and Frequency Band
  AT+TX: Get current image version and Frequency Band
  AT+<CMD>?        : Help on <CMD>
  AT+<CMD>         : Run <CMD>
  AT+<CMD>=<value> : Set the value
  AT+<CMD>=?       : Get the value
  error unknown
```

## Seen in logs but not in the firmware string table

Runtime-composed output (`AT+CFG` dump lines, command echoes with values
substituted) plus modem responses that come from the BG95 rather than the
STM32 application.

```
  180x  OK
   30x  AT+APN=NULL
   27x  AT+SERVADDR=66.33.22.220,33239
   27x  AT+PRO=3,5
   26x  AT+BKDNS=1,0,66.33.22.220,33239
   25x  AT+TLSMOD=0,0
   24x  AT+CLIENT=ps-cb
   24x  AT+MQOS=1
   23x  AT+MODEL=PS-CB,v1.2.1
   23x  AT+DEUI=869181074157478
   23x  AT+SLEEP=0
   23x  AT+3V3T=0
   23x  AT+5VT=0
   23x  AT+12VT=500
   23x  AT+PROBE=0000
   23x  AT+RXDL=0
   23x  AT+LDATA=NULL
   23x  AT+DNSCFG="8.8.8.8","8.8.4.4"
   23x  AT+CSQTIME=5
   23x  AT+GDNS=0
   23x  AT+SNI=0
   23x  AT+IPTYPE=1
   23x  AT+URI1=NULL
   23x  AT+URI2=NULL
   23x  AT+URI3=NULL
   23x  AT+URI4=NULL
   23x  AT+URI5=NULL
   23x  AT+INTMOD=0
   23x  AT+CLOCKLOG=1,65535,15,8
   23x  AT+GETLOG=2
   23x  AT+DOWNTE=0,0
   23x  AT+GNSST=60
   23x  AT+GPS=0
   23x  AT+GTDC=24
   23x  AT+QBAND=0x100002000000000f0e189f,0x10004200000000090e189f
   23x  AT+IOTMOD=2
   23x  AT+ROC=0,0,0,0
   23x  AT+REDPT=0
   23x  AT+NTP=NULL
   23x  AT+QCOPS=NULL
   23x  AT+OTASER=NULL
   23x  AT+OTACLT=NULL
   23x  AT+OTAUNAME=NULL
   23x  AT+OTAPWD=NULL
   23x  AT+OTATITLE=PS-CB
   23x  AT+OTAVER=v1.2.1
   22x  AT+PWD=***
   21x  AT+PWORD=***
   19x  AT+SUBTOPIC=dragino/ps-cb/down
   18x  AT+TDC=180
   18x  AT+PUBTOPIC=dragino/ps-cb/up
   16x  AT+UNAME=dragino
   13x  SERVADDR  = 66.33.22.220,33239 OK
   13x  CLIENT    = ps-cb OK
   13x  PWD       = *** OK
   13x  SUBTOPIC  = dragino/ps-cb/down OK
   13x  TLSMOD    = 0,0 OK
   13x  IPTYPE    = 1
   12x  PRO       = 3,5 OK
   12x  BKDNS     = 1,0,66.33.22.220,33239 OK
   12x  TDC       = 180 OK
   12x  UNAME     = dragino OK
   12x  PUBTOPIC  = dragino/ps-cb/up OK
   11x  MQOS      = 1 OK
    7x  AT+TDC=90
    5x  AT+UNAME=***
    5x  AT+PUBTOPIC=***/ps-cb/up
    5x  AT+SUBTOPIC=***/ps-cb/down
    4x  APN       = NULL OK
    4x  IOTMOD    = 2
    3x  APN       = NULL
    3x  AT+TIMESTAMP=2021-01-29 T00:00:41
    3x  AT+MQOS=0
    3x  AT+PRO=2,5
    3x  AT+SERVADDR=194.144.202.80,9999
    3x  AT+BKDNS=1,0,194.144.202.80,9999
    3x  AT+PRO=4,5
    3x  AT+SERVADDR=194.144.202.80,9998
    3x  AT+BKDNS=1,0,194.144.202.80,9998
    2x  AT+UNAME=NULL
    2x  AT+PUBTOPIC=NULL
    2x  AT+TDC=7200
    2x  AT+PRO=2,0
    2x  AT+BKDNS=1,0,NULL
    2x  AT+MQOS=2
    2x  AT+APN=iot.1nce.net
    2x  AT+APN=lpwa.vodafone.is
    2x  AT+TDC=?
    2x  AT+TIMESTAMP=2021-01-29 T00:01:00
    1x  AT+PWORD=<redacted>
    1x  AT+SERVADDR=NULL
    1x  AT+CLIENT=NULL
    1x  AT+PWD=NULL
    1x  AT+SUBTOPIC=NULL
    1x  AT+TIMESTAMP=2026-08-16 T15:16:12
    1x  AT+TIMESTAMP=2026-08-16 T15:33:56
    1x  AT+TIMESTAMP=2026-08-16 T15:34:08
    1x  AT+TIMESTAMP=2026-08-16 T15:38:47
    1x  AT+TIMESTAMP=2021-01-29 T00:00:37
    1x  AT+TIMESTAMP=2021-01-29 T00:00:46
    1x  APN       = iot.1nce.net OK
    1x  AT+TIMESTAMP=2021-01-29 T00:00:48
    1x  APN       = lpwa.vodafone.is OK
    1x  AT+TIMESTAMP=2021-01-29 T00:00:50
    1x  AT+TIMESTAMP=2021-01-29 T00:01:01
    1x  AT+TIMESTAMP=2021-01-29 T00:00:43
    1x  AT+TIMESTAMP=2021-01-29 T00:01:39
    1x  AT+TIMESTAMP=2026-08-16 T16:18:56
    1x  AT+UNAME:***
    1x  AT+PWD:***
    1x  AT+TIMESTAMP=2021-01-29 T00:00:42
    1x  AT+PRO=?
    1x  AT+SERVADDR=?
    1x  AT+BKDNS=?
    1x  AT+CLIENT=?
    1x  AT+PUBTOPIC=?
    1x  AT+SUBTOPIC=?
    1x  AT+TLSMOD=?
    1x  AT+MQOS=?
    1x  AT+APN=?
    1x  ERROR
    1x  AT+SERVADDR=54.36.178.49,1883
    1x  AT+BKDNS=1,0,54.36.178.49,1883
    1x  AT+TIMESTAMP=2021-01-29 T00:03:46
    1x  AT+TIMESTAMP=2026-08-16 T19:07:31
    1x  AT+TIMESTAMP=2021-01-29 T00:00:45
    1x  AT+TIMESTAMP=2026-08-16 T19:53:52
    1x  AT+TIMESTAMP=2026-08-16 T20:05:21
```
