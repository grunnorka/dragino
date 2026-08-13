# LTC2-CB - CONFIG / FIRMWARE RESULT

**Date:** 2026-08-06 (CubeProgrammer CLI attempt)  
**Status:** **FLASH FAILED** — STM32CubeProgrammer opens COM8 but **Activating device: KO** (no bootloader ACK)

## Install verified

| Item | Path / result |
|---|---|
| GUI | `C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32CubeProgrammer.exe` — **exists** |
| CLI | `C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe` — **exists** (v2.22.0) |
| Firmware | `C:\Users\Arnor\Downloads\dragino\LTC2-CB\firmware\LTC2-CB_v1.1.0.bin` (86116 bytes) |
| Target address | **0x08007800** (app only; bootloader at 0x08000000 not touched) |
| UART port | **COM8** (FTDI USB Serial) listed by `STM32_Programmer_CLI -l uart` |

## Flash command used

```text
STM32_Programmer_CLI.exe -c port=COM8 br=<baud> P=EVEN -d "firmware\LTC2-CB_v1.1.0.bin" 0x08007800 -v
```

Also tried: `P=NONE`, bauds 9600/38400/57600/115200/128000/230400, `rts=low dtr=high`, connect-only. All: **Activating device: KO**.

## Result

| Item | Value |
|---|---|
| Port open | Yes (COM8) |
| Bootloader activate | **KO** (timeout / no acknowledgement) |
| Download / verify | **not started** |
| Flash | **FAILED** |
| MQTT / MODEL | **not applied** (blocked on flash) |

## Hardware checklist before retry

1. **SW1 = ISP**
2. USB-TTL **GND connected** to board GND; sensor/baseboard GND jumpers **open** for flash
3. Crossed UART: USB **TXD → board RXD**, USB **RXD → board TXD**
4. Power on; press **RESET** during connect window
5. Only COM8 open (close PuTTY/monitor)
6. Re-run same CLI command; expect activate OK then download @ 0x08007800

## After flash succeeds (still pending)

1. Reconnect sensor/baseboard **GND** + **SW1 = Flash/normal**
2. Unlock PIN **358613**
3. MQTT: `AT+PRO=3,3` → **re-set** `AT+SERVADDR=167.235.104.181,1883` → `AT+UNAME=cdHsbYNjHJ7haAPkoJZD` → `AT+PWD=NULL` → topics → BKDNS → `AT+CLOCKLOG=1,65535,5,8` → `AT+TDC=1800`
4. Verify `AT+MODEL=?` → **v1.1.0**

## Return

| Field | Value |
|---|---|
| CLI path | `C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe` |
| Flash | **FAILED** (Activating device: KO) |
| MQTT | **not applied** |
| Next | Fix ISP wiring / RESET into bootloader, then re-run flash |
