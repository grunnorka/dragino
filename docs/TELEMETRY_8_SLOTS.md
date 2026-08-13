# ThingsBoard telemetry keys `1`–`8` — how they work

**Device:** PS-CB / PS-CB-NA (NB-IoT / LTE-M Analog Sensor)  
**Evidence:** Manual §2.2.1 / §2.2.3 / §3.9, `AT+CFG` dumps (`AT+CLOCKLOG=1,65535,15,8`, `AT+TDC=180`, `AT+PRO=3,3`), serial `AT+LDATA=…` payloads, ThingsBoard screenshot 2026-08-06.

---

## Short answer

Keys **`1`–`8` are not a ThingsBoard quirk**. They are **literal JSON keys** in the Dragino MQTT payload when **Clock Logging** is enabled. Each key is one historical sample of **IDC / VDC + sample time**. The device stores samples on a **CLOCKLOG interval** (here **15 minutes**), and every **TDC** MQTT publish includes the **last N** of those samples (here **N = 8**) in one message. ThingsBoard therefore shows the same “Last update time” on all eight keys (receive time), while the **inner ISO timestamps** show when each sample was actually taken.

---

## 1. Why keys are numbered `1`–`8`

From the manual (JSON / ThingsBoard Type=3 payload notice):

> Json entry **1 ~ 8** are the last **1 ~ 8** sampling data as specify by **`AT+CLOCKLOG=1,65535,15,8`**. Each entry includes (from left to right): **Idc_input, Vdc_input, Sampling time**.

So:

| What you see | Source |
| --- | --- |
| Telemetry keys `1` … `8` | Device JSON fields named `"1"` … `"8"` |
| Count of keys | Parameter **`d`** of `AT+CLOCKLOG` (uplink entry count, max **32**) |
| ThingsBoard behavior | Flattens each top-level JSON key into a telemetry key; all updated in one publish |

This device’s CFG repeatedly shows:

```text
AT+CLOCKLOG=1,65535,15,8
```

→ **8** history slots per uplink. Manual Type=3 example uses the same `"1"`…`"8"` shape.

Other payload fields (`idc_input`, `vdc_input`, `battery`, `signal`, `time`, GPS, etc.) are **live values at uplink time** and may appear as separate TB keys if the full object is stored. The numbered keys are **only** the clock-log buffer.

---

## 2. What each array element means

Example from the screenshot:

```text
[4.034, 0.000, "2026-08-06T12:51:13Z"]
```

| Index | Meaning | Channel |
| --- | --- | --- |
| `[0]` | **IDC input** (current / IDC channel reading) | Analog IDC |
| `[1]` | **VDC input** (voltage / VDC channel reading) | Analog VDC |
| `[2]` | **Sampling time** (ISO 8601 UTC) | When that sample was logged into memory |

Not battery voltage: battery is a separate top-level field (`"battery": 3.xxx` in `AT+LDATA`). In the screenshot, ~`4.034` on newer slots matches recent **IDC** activity; older slots show `[0.000, 0.000, …]` when both channels were idle/zero at sample time.

---

## 3. Rolling history buffer in one MQTT publish?

**Yes.** Behavior is:

1. With Clock Logging enabled, the node records IDC/VDC (+ timestamp) into onboard memory on the **CLOCKLOG sample interval**.
2. Memory holds up to **32** historical groups (`AT+CDP` can read/clear them).
3. On each **TDC** uplink, the MQTT JSON includes the **last `d` entries** as keys `"1"` (newest of the included set) … `"d"`.
4. ThingsBoard receives **one** publish → same **Last update time** for keys `1`–`8`, even if inner sample times span hours or days.

So TB “Last update time” = **MQTT receive / store time**. Inner timestamps = **sample times**.

Serial confirmation (same shape as Type=3 manual example), e.g. `logs/20260806_123854_cfg_verify.raw.log`:

```json
"1":[0.000,0.000,"2026-08-06T12:09:24Z"],
"2":[0.000,0.000,"2026-08-06T11:54:47Z"],
…
"8":[0.000,0.000,"2026-08-05T16:31:32Z"]
```

---

## 4. Which AT settings control this

### Primary: `AT+CLOCKLOG=a,b,c,d` (manual §3.9)

| Param | Role | This unit |
| --- | --- | --- |
| **a** | `0` = disable; `1` = enable | `1` |
| **b** | First sample start second (`0`–`3599`, or **`65535`** = start after first network/uplink packet) | `65535` |
| **c** | **Sampling interval in minutes** (`0`–`255`) | **`15`** → ~15 min between inner timestamps |
| **d** | **How many history entries in each TDC uplink** (max **32**) | **`8`** → keys `1`–`8` |

Examples from the manual:

- `AT+CLOCKLOG=1,65535,1,3` → sample every **1 min**, uplink last **3** records  
- `AT+CLOCKLOG=1,65535,15,8` → sample every **15 min**, uplink last **8** (this device / downlink example `0301FFFF0F08`)  
- Disable logging: `AT+CLOCKLOG=1,65535,0,0` (per manual note)

### Related (not what creates keys `1`–`8`)

| Setting | Role |
| --- | --- |
| **`AT+TDC=<seconds>`** | How often the device **publishes** (MQTT uplink). Here **`180`** = every **3 minutes**. Does **not** set the ~15 min sample spacing. |
| **`AT+PRO=3,3`** | MQTT + **ThingsBoard payload type**. Same numbered history keys appear in Type=5 JSON; Type=3 is the TB-oriented profile (and rewrites default server — see `HIVEMQ_BROKER_SWITCH.md`). |
| **`AT+STDC=…`** | Different feature: burst multiple IDC **or** VDC reads, then one uplink. **Not** the source of keys `1`–`8`. |
| **`AT+CDP`** | Read / clear the cached historical records (up to 32). |

There is **no `NOUD`** command in the PS-CB-NA material in this workspace; history uplink count is **`CLOCKLOG` `d`**, not a separate NOUD setting.

### Channel mapping

- Array `[0]` / top-level `idc_input` → **IDC** analog channel  
- Array `[1]` / top-level `vdc_input` → **VDC** analog channel  
- Keys `1`–`8` are **time-indexed history of those two channels**, not eight physical inputs.

---

## 5. `TDC=180` vs ~15 minute spacing in timestamps

Two independent timers:

```text
CLOCKLOG c=15 min  →  write sample to memory every ~15 minutes
TDC = 180 s        →  MQTT publish every ~3 minutes (includes last 8 samples)
```

Consequences:

1. **Inner timestamps** advance about every **15 minutes** when logging is healthy (`c=15`). Gaps larger than 15 min (e.g. overnight jump between key 5 and 6 in the screenshot) mean **no sample was logged** in that window (power, coverage, reset, logging not yet started, etc.).
2. **ThingsBoard Last update time** can refresh every **~3 minutes** (TDC) even when the eight sample times have not changed yet — the device **re-publishes the same rolling buffer** until the next CLOCKLOG sample is taken, then key `1` becomes the new sample and older ones shift.
3. Do **not** expect TDC=180 to produce 3-minute spacing inside the arrays; that would require `AT+CLOCKLOG=…,c,…` with **`c` in minutes** set accordingly (and note `c` is minutes 0–255, not seconds).

Screenshot check (inner times): `12:51` → `12:36` (~15 min), `11:54` → `11:39` (~15 min); larger gaps elsewhere = missed / sparse logging, not TDC.

---

## Mental model

```text
[Analog IDC/VDC]
      │
      ▼  every c minutes (CLOCKLOG)
[On-device history buffer ≤32]
      │
      ▼  every TDC seconds (here 180)
[One MQTT JSON: live fields + "1"…"d" history]
      │
      ▼
[ThingsBoard: keys 1–8, same Last update time]
```

---

## Useful commands

```text
AT+CLOCKLOG=?
AT+TDC=?
AT+PRO=?
AT+CDP          # inspect / clear cached history
AT+CFG          # full dump (expect CLOCKLOG=1,65535,15,8 on this unit)
```

To change slot count or sample spacing (takes effect per manual notes / often after time sync / reset):

```text
AT+CLOCKLOG=1,65535,<minutes>,<count>   # e.g. 15,8 or 5,16
AT+TDC=180                               # publish cadence only
```

---

## Sources

- `../PS-CB-NA/manuals/PPS-CB-NA - NB-IoT_LTE-M Analog Sensor.md` — §2.2.1 notice (entries 1–8), §2.2.3 Type=3 example, §3.9 CLOCKLOG, §3.8 STDC, §3.11 CDP  
- CFG / LDATA: `logs/20260806_123854_cfg_verify.raw.log` and related `*_cfg_*.raw.log` / `*_mqtt_*.raw.log`  
- Broker/profile context: `HIVEMQ_BROKER_SWITCH.md`, `AT_COMMANDS_HANDOFF.md`
