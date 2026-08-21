# PS-CB-NA — firmware update & bootloader recovery (LLM playbook)

**Audience:** an LLM agent or operator flashing a PS-CB-NA over USB-TTL from a cold start.
**Validated on:** 2026-08-16, Fedora Linux, `/dev/ttyUSB0` (FTDI FT232), Python 3.14 venv.
**Outcome of that session:** device was dark (no LED, no serial output) because its
**bootloader had been erased**. Restored bootloader v1.3 + app v1.2.1; device boots,
attaches to the network and accepts AT commands again.

**Live MQTT status / next steps:** [HANDOFF.md](HANDOFF.md) (do not re-flash a healthy board).  
Companion docs: [SETUP.md](SETUP.md) (MQTT/Railway config), [../docs/RAILWAY_MQTT.md](../docs/RAILWAY_MQTT.md).
Official guide: [UART/TTL Upgrade for NB-IoT/LTE-M Devices](https://wiki.dragino.com/docs/NB-IoT/firmware-update/uart-ttl-upgrade-for-nb-iot-lte-m-devices/).

---

## 1. The one mistake that bricks this board

The flash has **two independent images**. Writing the app over the bootloader is
the classic way to kill the device:

| Image | Address | Size (this unit) | Flash pages |
|---|---|---|---|
| Dragino NB bootloader | `0x08000000` | 25 824 B → padded 25 856 B | 0–201 |
| Application firmware | `0x08007800` | 112 504 B → padded 112 512 B | 240–1118 |

* A **`.hex` carries its own addresses** — `PS-CB-NA_v1.2.1.hex` starts at `0x08007800`.
  Never relocate it.
* A **`.bin` carries none**. `DRAGINO NB bootloader v1.3.bin` belongs at `0x08000000`.
  If you ever write an *app* `.bin` without setting `0x08007800`, tools default to
  `0x08000000` and destroy the bootloader. Dragino documents the exact symptom:
  *"the LED indicator light will not respond, and there will be no content when
  viewing the node startup information."*

**Symptom → cause map**

| Symptom | Meaning |
|---|---|
| No LED, completely silent UART in Flash mode | Bootloader erased or overwritten. Rewrite both images. |
| No LED but ROM bootloader answers on `0x7F` | Normal for **ISP mode** — the app does not run in ISP. Not a fault. |
| `DRAGINO NB bootloader v1.3` prints, then nothing | Bootloader fine, app missing or corrupt. Rewrite app only. |

### STM32L0 erased flash reads as `0x00`, not `0xFF`

This cost real debugging time. On the STM32L072 in this device, an erased flash
page reads back **all zeros**. So a bootloader region full of `00 00 00 00` is
**blank/erased**, not "programmed". Do not conclude the image is present just
because the bytes are non-`0xFF`.

The STM32L0 and L1 families are the exception among STM32 parts: their
non-volatile memory is EEPROM-based (byte-organised) and erases to `0`, whereas
other STM32 families use sector-organised flash that erases to `1`
([ST community](https://community.st.com/stm32-mcus-products-25/why-is-erased-flash-not-reading-ff-74957),
[EFTON gotcha 98](http://efton.sk/STM32/gotcha/g98.html)).

The reliable check is therefore the vector table: word 0
must be a plausible initial stack pointer (`0x2000xxxx`) and word 1 a reset
handler in flash. For bootloader v1.3 the real values are:

```
initial SP      0x20001ff8
reset handler   0x080000dd
first 16 bytes  f8 1f 00 20 dd 00 00 08 41 38 00 08 d5 37 00 08
```

**How this unit was bricked:** an earlier attempt used a flow that performed a
**global mass erase** before writing only the app. Mass erase wipes page 0
onward, taking the bootloader with it. Always erase **only the pages you are
about to write**, and never page 0 unless you are deliberately writing the
bootloader.

---

## 2. Hardware setup

| Item | Value |
|---|---|
| Wiring | USB-TTL `GND↔GND`, `TX↔RX`, `RX↔TX` (only these three) |
| `SW1` = **ISP** | Flashing. App does not run, LED stays off — expected. |
| `SW1` = **Flash** | Normal operation, AT console, boot banner. |
| Flashing link | **115200 8E1** (even parity — ST ROM bootloader requirement) |
| AT console | **9600 8N1** |
| RESET | Press **after** setting SW1 to ISP, **before** the tool synchronises |

`DTR`/`RTS` are **not** wired to `RESET`/`BOOT0` on this board, so automatic entry
into the bootloader is impossible. The RESET press is genuinely manual — the
scripts below block on a desktop popup until you confirm it.

---

## 3. Environment (Linux)

```bash
python3 -m venv .venv
.venv/bin/pip install pyserial paho-mqtt intelhex stm32loader
sudo chmod 666 /dev/ttyUSB0          # or add your user to the dialout group
```

`.env` in the repo root (gitignored):

```
DRAGINO_PIN=your_6_digit_pin
DRAGINO_PORT=/dev/ttyUSB0
DRAGINO_BAUD=9600
```

### Two environment traps for agents

1. **The sandbox hides `/dev/ttyUSB0`.** `ls /dev/ttyUSB*` reports "No such file"
   inside the restricted sandbox even though the port exists. Every command that
   touches the serial port (or shows a popup) needs full permissions. Verify the
   adapter is physically present with `lsusb | grep -i ft232` before assuming the
   device is unplugged.
2. **Never pipe a long flashing run through `head`/`tail`.** It buffers all output
   until completion, so a healthy 8-minute run looks like a hang. Let it stream.

---

## 4. Firmware files

Place both in `PS-CB-NA/firmware/`:

| File | Role |
|---|---|
| `PS-CB-NA_v1.2.1.hex` | application, self-addressed at `0x08007800` |
| `DRAGINO_NB_bootloader_v1.3.bin` | bootloader, must be written to `0x08000000` |

Sources: NB firmware on Dragino's Dropbox; the BLE bootloader from the
"How to upload bootloader" section of the official UART/TTL upgrade page.

Sanity-check a new image before flashing:

```bash
.venv/bin/python - <<'PY'
from intelhex import IntelHex
ih = IntelHex("PS-CB-NA/firmware/PS-CB-NA_v1.2.1.hex")
print(hex(ih.minaddr()), hex(ih.maxaddr()), ih.maxaddr()-ih.minaddr()+1, len(ih.segments()))
PY
# expect 0x8007800 0x8022f77 112504 1
```

---

## 5. Flashing procedure

```bash
.venv/bin/python PS-CB-NA/scripts/recover_pscb.py
```

`recover_pscb.py` writes **bootloader then app in a single ISP session** (one
RESET press), and it refuses to run if the hex does not start at `0x08007800` or
if the bootloader image would reach into the app region.

It will:

1. Pop up **"Put the board into ISP mode"** → set `SW1=ISP`, press RESET, confirm.
2. Synchronise with the ROM bootloader, print `ROM bootloader version: 0x31`,
   `Chip id: 0x447` (STM32L072).
3. Dump the current vector tables of both regions so you can see what was there.
4. Erase only the target pages, write, and **read back every 4 KiB block to
   compare byte-for-byte**.
5. Pop up **"Back to normal (Flash) mode"** → set `SW1=Flash`, confirm, press RESET.
6. Listen at 9600 and show the boot banner.

Flags: `--skip-bootloader` (app only), `--skip-app` (bootloader only),
`--port`, `--baud`.

Expected duration: about 3 minutes for both images.

### stm32loader specifics that matter (v0.7.1)

| Detail | Why |
|---|---|
| `device_family="L0"`, page size **128 B** | Wrong page size → `PageIndexError` about page boundaries. |
| Pad images to a 128-byte multiple | An unaligned length triggers the same error. |
| Erase in batches of **≤128 pages** | Passing ~880 pages at once raises `struct.error: 'H' format requires 0 <= number <= 65535`. |
| Parity `"E"` at 115200 | ST ROM bootloader protocol (AN3155). |
| Write + read-back in 4 KiB blocks | The high-level helper reported a bogus `DataMismatchError` at address `0x0`; explicit `write_memory_data`/`read_memory_data` per block is reliable and pinpoints real mismatches. |
| **Do not call `get_flash_size()`** | On L0 it reads a register the bootloader NACKs: `CommandError: NACK 0x11 address failed`. Harmless to skip; it is cosmetic. |
| `SYNCHRONIZE_ATTEMPTS = 10` | Gives the manual RESET press room to land. |

If sync fails with `Bad reply from bootloader`, the board is not in ISP or RESET
was not pressed. Re-press RESET and retry — nothing is written before sync.

---

## 6. Post-flash verification

```bash
.venv/bin/python PS-CB-NA/scripts/verify_pscb.py
```

Prompts for a RESET, captures 60 s of boot log, checks for the bootloader banner,
image version and modem attach, then unlocks with the PIN and dumps `AT+CFG`.

A healthy boot on this unit looks like:

```text
DRAGINO NB bootloader v1.3
AT                                  <- MCU probing the BG95 module, normal
AT+NAME869181074157478
[30016]Battery: 3.579 V
[30096]IDC : 0.000 mA;VDC: 0.000 V
DRAGINO PS-CB SensorManual
Image Version: v1.2.1
NB-IoT Stack : D-BG95-004
Protocol in Used: UDP               <- factory default, becomes MQTT after AT+PRO=3,5
[39464]NB module is initializing...
[48767]NBIOT has responded.
[51448]Model information:BG95-M2.
[52787]The IMEI number is:869181074157478.
[54134]The IMSI number is:901280043992225.
[61173]Signal Strength:31
[66211]Network Information:"eMTC","27402","LTE BAND 8",3600
```

Then `Password Correct` after sending the PIN.

**Timing:** the device sleeps between uplinks. The reliable moment to log in is
the **boot window**; repeated PIN attempts during modem init eventually land.
`Password Incorrect` appearing once or twice before `Password Correct` is normal —
it happens when the PIN arrives while the firmware is mid-initialisation.

Fresh firmware resets the configuration to factory defaults
(`SERVADDR=NULL`, `PRO=2,0`, `TDC=7200`), so re-apply the MQTT config afterwards.

---

## 7. This unit's identity

| Field | Value |
|---|---|
| Model | `PS-CB,v1.2.1` |
| AT PIN (`AT+PWORD`) | from `.env` `DRAGINO_PIN` |
| IMEI / `AT+DEUI` | `869181074157478` |
| IMSI | `901280043992225` |
| MCU | STM32L072, chip id `0x447`, ROM bootloader `0x31` |
| Modem | Quectel **BG95-M2**, stack `D-BG95-004` |
| Radio | eMTC (LTE-M), `AT+IOTMOD=2`, operator `27402` (Vodafone Iceland), band 8 / 20, signal 31 |

### SIM and APN — correct answer for this unit

IMSI prefix **`90128` is Vodafone GDSP** (Vodafone's global IoT roaming SIM),
**not 1NCE**. Older notes in this repo that treat `901…` IMSIs as 1NCE are wrong
and led to a bad APN guess.

**Leave `AT+APN` unset (`NULL`) — the network supplies the APN.** Measured
behaviour, all with identical MQTT settings:

| `AT+APN` | PDP context | DNS / NTP | MQTT result |
|---|---|---|---|
| `NULL` (correct) | activated first try | `DNS configuration is successful`, time OK | `Failed to open the MQTT client network` |
| `iot.1nce.net` | `Failed to activate PDP context`, then OK | `DNS configuration failed`, `Failed to get time` | `MQTT configuration failed` |
| empty string | `Failed to activate PDP context`, then OK | `Failed to get time` | `MQTT configuration failed` |

Note that **`AT+APN=` (empty value) is not the same as `NULL`** and is measurably
worse. To truly clear it use `AT+APN=NULL` and confirm `AT+CFG` reports
`AT+APN=NULL`.

---

## 8. Reconfiguring after a flash

```bash
.venv/bin/python PS-CB-NA/scripts/fix_pscb_mqtt.py          # read, repair, verify, ATZ
.venv/bin/python PS-CB-NA/scripts/reset_and_apply_pscb.py   # AT+FDR1, then write everything
```

`fix_pscb_mqtt.py` reads the authoritative `AT+CFG` dump, writes only the fields
that are wrong (each acknowledged individually), reboots with `ATZ`, and re-reads
to prove persistence. `reset_and_apply_pscb.py` does the same but starts from a
factory reset, so it also clears any field nobody thought to check. See
[SETUP.md](SETUP.md) for the target values.

### What `AT+FDR1` actually resets on v1.2.1

Measured 2026-08-16. `AT+FDR1` (factory defaults **except** passwords) is much
narrower than the name suggests — it left every MQTT identity field untouched:

| Reset to default | Left alone |
|---|---|
| `PRO` → `2,0` (UDP), `TDC` → `7200`, `MQOS` → `0`, `BKDNS` → `1,0,NULL` | `SERVADDR`, `CLIENT`, `UNAME`, `PWD`, `PUBTOPIC`, `SUBTOPIC`, `APN`, `IOTMOD`, `PWORD` |

Two consequences:

* **`AT+FDR1` never returns `OK`.** The device reboots immediately, so a strict
  request/response helper reports `NO ACK`. Confirm it worked by reading `AT+CFG`
  after the reboot (`PRO=2,0` and `TDC=7200` are the tell), not by the ack.
* It does **not** clear a bad `SERVADDR`/`UNAME`/`PWD`. To wipe those you need
  `AT+FDR`, which also resets the AT password — only do that if you are certain
  the sticker PIN is the factory value.

Because `PRO` goes back to `2,0`, an `ATZ` is always required afterwards; the
next boot banner must read `Protocol in Used: MQTT`.

### AT-over-UART pitfalls (why `shared/at_session.py` exists)

The firmware prints asynchronous progress lines (`[48768]NBIOT has responded.`)
**while AT commands are in flight**. A fixed "send, then read for 2 s" loop
therefore attributes replies to the wrong command — in one run the answer to
`AT+CLIENT=?` (`ps-cb`) was recorded as the value of `PUBTOPIC`, producing a
completely fictional config report.

Rules:

* Ignore lines matching `^\[\d+\]`, plus `AT+PWRM…` and `AT+NAME…`, which the
  firmware emits on its own schedule.
* Wait for a real `OK` / `ERROR` terminator per command (`at_session.at_cmd`).
* Prefer **`AT+CFG`** as one authoritative dump over many `AT+X=?` queries
  (`at_session.read_cfg`).
* `AT+PRO` replies `Attention:Take effect after ATZ` — **`ATZ` is mandatory**
  before the new protocol is used. `Protocol in Used:` in the next boot banner
  confirms it (`UDP` → `MQTT`).
* On this firmware a fresh unit had `UNAME` and `PUBTOPIC` at `NULL` and `MQOS=2`
  even though `SERVADDR` looked right; that produces
  `MQTT parameter configuration error` at upload time. Always verify every field.

---

## 9. Telling a human to press a button

Chat messages are easy to miss, so hardware steps use
`shared/prompt_user.py`, which for every step raises a **blocking `zenity`
dialog**, a **`notify-send` desktop notification**, and a terminal banner, and
aborts cleanly if the user cancels. It falls back to `input()` when no desktop
session is available. Use `prompt_user.step(...)` for anything requiring a
switch move or button press; never bury such an instruction in prose.

---

## 10. Script inventory (prefer these)

| Path | Purpose |
|---|---|
| `PS-CB-NA/scripts/recover_pscb.py` | Write bootloader + app in one ISP session, with verification |
| `PS-CB-NA/scripts/verify_pscb.py` | Capture boot banner, unlock, dump config |
| `PS-CB-NA/scripts/fix_pscb_mqtt.py` | Read/repair/verify MQTT params, `ATZ`, persistence check |
| `PS-CB-NA/scripts/reset_and_apply_pscb.py` | `AT+FDR1`, then write the full config from scratch and prove persistence |
| `PS-CB-NA/scripts/diag_pscb_ports.py` | IP-only A/B Railway vs public broker (handshake test) |
| `shared/prompt_user.py` | Blocking on-screen prompts for hardware actions |
| `shared/at_session.py` | Async-safe AT request/response + `AT+CFG` parser |
| `shared/set_mqtt_secret.py` | Masked password prompt, verified against broker, saved gitignored |

`PS-CB-NA/scripts/flash_pscb.py` is the **older app-only** flasher. Prefer
`recover_pscb.py`; it covers the bootloader too and does the same page-safety
checks. Older `configure_pscb_*.py` scripts are Windows COM-era; on Linux use
`fix_pscb_mqtt.py` / `reset_and_apply_pscb.py` instead.

---

## 11. Known-open issue: the MQTT handshake, not the port

After a factory reset and a full re-apply (`reset_and_apply_pscb.py`,
2026-08-16), all 12 parameters read back correct and survive `ATZ`, yet no
message reaches the broker. **The failure is the MQTT `CONNECT`, not the TCP
socket** — proven by two consecutive upload cycles in one session:

```text
[72915]*****Upload start:0*****
[78356]Failed to open the MQTT client network     <- socket refused/timed out
[80908]Failed to close TCP connection
[81951]Failed to send

[292892]*****Upload start:1*****
[300346]Opened the MQTT client network successfully   <- same IP, same port, OK
[304912]Failed to send                                <- ~4.5 s later, no CONNACK
```

The second cycle opened TCP to `66.33.22.220:33239` **successfully**, so the
earlier conclusion that the carrier blocks Railway's high port is **wrong**: the
port works, intermittently on first attempt and reliably by the second. What
never appears is `Successfully connected to the server`, which the firmware
prints on `CONNACK`.

Established facts:

* The broker is healthy — authenticating to `altaria.proxy.rlwy.net:33239` from a
  PC returns `CONNECT 0`, with the same username and password the device holds.
* All 12 device MQTT parameters read back correct and survive `ATZ`
  (`PRO=3,5`, `TLSMOD=0,0`, `MQOS=1`, `APN=NULL`, `IPTYPE=1`).
* With `APN=NULL` the PDP context activates first try and DNS succeeds.
* TCP to port 33239 **does** open from the modem.
* No `MQTT configuration failed` and no `parameter configuration error` in this
  session, so the client is no longer misconfigured.

So the open question is why the broker sends no `CONNACK` within the firmware's
~4.5 s window. Candidates, in order of cheapness to test:

| Hypothesis | Test |
|---|---|
| `CONNACK` is slower than the firmware's timeout through Railway's TCP proxy | Retry with a broker whose RTT is short, or watch the broker log for an inbound `CONNECT` at that timestamp — if mosquitto logs the attempt, the device is giving up too early |
| Broker rejects the `CONNECT` (bad client id / credentials as the device encodes them) | Watch mosquitto logs during an upload cycle |
| `MQOS=1` needs a `PUBACK` the proxy path never returns | Set `AT+MQOS=0` and retry |
| Device MQTT client incompatibility with this broker version | `diag_pscb_ports.py` phase B: `54.36.178.49:1883`, anonymous, IP only |

`diag_pscb_ports.py` is still the right A/B harness for “does any broker complete
the handshake?”. See [HANDOFF.md](HANDOFF.md) for the ordered next tests.
