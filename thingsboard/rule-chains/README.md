# ThingsBoard rule chain: Dragino (PS-CB / PS-CB-NA)

Importable rule chain that:

1. Unpacks CLOCKLOG keys `"1"`…`"32"` into real timeseries points using each sample’s inner timestamp
2. Keeps raw `idc_input` / `vdc_input` and adds `depth_cm` / `depth_mm`
3. Moves identity / GPS / alarms to **client attributes**
4. Reads probe full-scale from server attribute `probe_full_scale_cm` (`100` or `200`, default **100**)

Files:

- [dragino.json](dragino.json) — importable rule chain
- [parse-dragino-uplink.tbel](parse-dragino-uplink.tbel) — script body (also embedded in the JSON; keep in sync if you edit by hand)

## Import

1. In ThingsBoard: **Rule chains** → **Import rule chain**
2. Select `thingsboard/rule-chains/dragino.json`
3. Open the imported **Dragino** chain and confirm nodes:
   - **Get probe_full_scale_cm** → **Parse Dragino uplink** → **Route parsed** → Save Attributes / Save Timeseries
4. If import warns about **Generate Report** (PE-only rule-chain id), either keep it (PE) or delete that node and its connection on CE

## Assign to devices

1. Open the device profile used by PS-CB / PS-CB-NA (or create one)
2. Set **Default rule chain** to **Dragino**  
   - Or leave Root as default and add a “Rule Chain” node that forwards device traffic into Dragino
3. Save the profile and ensure devices use it

## Configure probe scale (100 cm vs 200 cm)

On each device (Attributes → **Server attributes**), add:

| Key | Value | Meaning |
| --- | --- | --- |
| `probe_full_scale_cm` | `100` | 4–20 mA → 0–100 cm (0–1000 mm) |
| `probe_full_scale_cm` | `200` | 4–20 mA → 0–200 cm (0–2000 mm) |

If the attribute is missing, the script defaults to **100**.

Formula (raw mA kept unchanged):

```text
depth_cm = (idc_input - 4) / 16 * probe_full_scale_cm
depth_mm = depth_cm * 10
```

Depth keys are omitted when `idc_input < 4`. Depth is clamped to `[0, probe_full_scale_cm]`.

## Data model after transform

### Client attributes (from uplink)

`IMEI`, `IMSI`, `Model`, `latitude`, `longitude`, `gps_time`, `idc_alarm`, `vdc_alarm`

### Telemetry

| Key | Notes |
| --- | --- |
| `idc_input`, `vdc_input` | Raw values (live + each CLOCKLOG sample) |
| `depth_cm`, `depth_mm` | Derived from `idc_input` + `probe_full_scale_cm` |
| `battery`, `signal`, `interrupt`, `interrupt_level`, `time` | Live uplink only |
| `"1"`…`"N"` arrays | **Not saved** (unpacked into historical points) |

History samples use the ISO timestamp inside each CLOCKLOG array as the ThingsBoard `ts`.

## Flow

```text
POST_TELEMETRY
  → Get probe_full_scale_cm (server attr → metadata.ss_probe_full_scale_cm)
  → Parse Dragino uplink (TBEL)
  → Route parsed
       ├─ Post attributes → Save Attributes (CLIENT_SCOPE)
       └─ Post telemetry  → Save Timeseries
```

## Verification checklist

After the next device uplink:

1. **Attributes** (Client): `IMEI`, `IMSI`, `Model` present; GPS/alarms updated if sent
2. **Attributes** (Server): `probe_full_scale_cm` set to `100` or `200`
3. **Latest telemetry**: no keys `1`…`N` as JSON arrays
4. **Telemetry**: `idc_input` still present (raw mA); `depth_cm` / `depth_mm` present when idc ≥ 4
5. Chart `idc_input` or `depth_cm` over several hours: points align with CLOCKLOG sample times (not only TDC uplink times)

## Optional cleanup

Existing timeseries keys `1`…`N` stored before this chain was applied remain in history. Delete those keys from the device telemetry UI if you no longer want them in Latest telemetry.
