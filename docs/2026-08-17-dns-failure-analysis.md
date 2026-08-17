# DNS failure analysis — Vodafone GDSP restricted APN vs Síminn

Date: 2026-08-17
Scope: PS-CB-NA open firmware (`ps-cb-openfw`), Quectel BG95-M2. Why does the
DNS (hostname → IP) step fail/stall on the Vodafone GDSP SIM but appear to work
on Síminn?
Pure research/analysis. No serial access, no flashing. No secrets in this doc.

Companion doc: `2026-08-17-vodafone-vs-siminn-vakt-ab.md` (the TCP-reset A/B —
that test ran with `GDNS=0`, so it exercised the BKDNS static-IP path, *not* a
real DNS lookup; see "Reconciling with the A/B doc" below).

---

## 1. Evidence timeline (from logs)

### 1a. Today, 2026-08-17 — open firmware, BOTH SIMs ran `GDNS=0`

`bench-logs/2026-08-17-vodafone-bench-raw.log` (binary; text-forced grep) and
`logs/20260817_114014_pscb_tb_bench.raw.log` both show the console config echo:

```text
AT+DNSCFG="8.8.8.8","8.8.4.4"
AT+BKDNS=1,0,66.33.22.220,33239
AT+GDNS=0
```

With `GDNS=0` the firmware **never issues `AT+QIDNSGIP`**. It prints the BKDNS
host directly:

- Vodafone (`bench-raw`): `[12447]Domain IP:66.33.22.220` ~1.8 s after
  `*****Upload start:0*****` — this is the **BKDNS fallback print**, not DNS.
  After the harness repointed BKDNS to the vakt IP: `[12447]Domain IP:167.235.104.181`.
- Síminn (`114014`, IMSI `274012011267761`): `[17804]Domain IP:167.235.104.181`
  ~1.8 s after `Upload start:0`. Again the BKDNS print — **no `Resolving domain
  name...` line precedes it anywhere in the log.**

There is **no `Resolving domain name...` line in any openfw log from today**
(verified by grep across all `20260817*` logs). So today neither SIM actually
performed an on-air DNS query; both used the static BKDNS IP.

### 1b. Real `GDNS=1` DNS lookups — stock LTC2-CB firmware (Aug 7), fast-fail signature

The openfw "Resolving → Domain IP" round-trip timing is best seen in the stock
firmware logs (same BG95 radio, same strings). These show the *failure*
signature on a restricted/filtered path:

`logs/20260807_103247_atz_no_pro_persist.raw.log`:

```text
[97307]Resolving domain name...
[101833]Domain name resolution failed      <- +4526 ms
[106860]Domain IP:18.156.19.212,1883       <- BKDNS fallback ~5 s later
```

`logs/20260807_123856_ltc2_atz.raw.log` (repeated attempts):

```text
[60770]Resolving domain name...
[63803]Domain name resolution failed      <- +3033 ms
[68838]Domain name resolution failed      <- next retry
[73873]Domain name resolution failed
[78909]Domain name resolution failed
```

Stock firmware fails a DNS attempt in ~3–4.5 s and retries on a ~5 s cadence.

### 1c. Our open firmware's ~60 s stall (the "Vodafone DNS stall")

When `GDNS=1` and `SERVADDR` is a hostname, `app/uplink.c::resolve_host()` sends
`AT+QIDNSGIP=1,"<host>"` and waits **30 s** for the result URC, then retries —
**2 tries × 30 s = ~60 s** — before declaring failure and falling back to BKDNS.
That 60 s is a *firmware-imposed* wait (two 30 s URC windows), exactly matching
the "~60 s stall then BKDNS fallback" observed on Vodafone.

---

## 2. Firmware DNS path summary (as implemented today)

`app/uplink.c::resolve_host()` decision tree:

1. `SERVADDR` empty → `MQTT parameter configuration error`, end cycle.
2. `SERVADDR` is a numeric IPv4 literal → `No DNS resolution required`, use it
   directly (no modem traffic).
3. Else if `g_cfg.gdns != 0` → print `Resolving domain name...`, send
   `AT+QIDNSGIP=1,"<servaddr>"`; wait up to 30 s for the result, retry once
   (2×30 s). On a parsed IP → `Domain IP:<ip>`. Else → `Domain name resolution
   failed`.
4. Regardless of the above, if `g_cfg.bkdns_en && bkdns_host` set → print
   `Domain IP:<bkdns_host>` and use the BKDNS host/port (the silent fallback).

Two implementation facts that matter for the diagnosis:

- **The firmware never sends `AT+QIDNSCFG`.** `grep -ri QIDNSCFG app/` → no
  matches. The `DNSCFG="8.8.8.8","8.8.4.4"` value is stored in EEPROM and shown
  in `AT+CFG` / accepted by `AT+DNSCFG=` (console.c `set_dnscfg`), but it is
  **never pushed to the modem**. So `AT+QIDNSGIP` resolves using whatever DNS
  the modem currently holds for context 1.
- **The URC parser only matches `+QIDNSGIP: 1,...`.** It searches for the
  substrings `"+QIDNSGIP: 1,"` / `"QIDNSGIP: 1,"`. Per the Quectel manual the
  BG95's asynchronous result is `+QIURC: "dnsgip",<err>,<IP_count>,<DNS_ttl>`
  followed by `+QIURC: "dnsgip",<host_IP_addr>` — a **different string** our
  parser never matches (see §3 and hypothesis D).

---

## 3. Quectel BG95 DNS mechanics (web sources)

Sources:
- Quectel *BG95&BG77&BG600L Series TCP/IP Application Note V1.2* (sixfab mirror
  PDF, and Quectel forums short-url PDF) — §2.3.13 `AT+QIDNSCFG`, §2.3.14
  `AT+QIDNSGIP`, ch.4 error codes.
- Quectel forum threads: "BG95 DNS failure" (t/11078), "Bg95 dns failed"
  (t/34374), "BG96 DNS config AT Command throwing ERROR" (t/9663),
  "AT+QIDNSCFG Configure Address of DNS Server command" (t/25163).

Findings:

- **`AT+QIDNSCFG=<cid>[,<pri>[,<sec>]]`** — set/query DNS servers for a PDP
  context. "Before setting the DNS server address, the host must activate the
  context with `AT+QIACT` first." Max response 300 ms. **"The command takes
  effect immediately. The configurations will not be saved."** (not persistent
  across reboot / PDP re-activation).
- **Default when no `QIDNSCFG` is set:** the context uses the **DNS servers
  assigned by the network at PDP activation** (delivered in the PDP Context
  Activation accept / PCO, i.e. carrier DHCP-equivalent). This is why stock
  firmware resolved fine with *no* user `AT+DNSCFG` (our own MODEM.md §5 note:
  "success was measured with no DNSCFG set — likely network-provided DNS").
- **`AT+QIDNSGIP=<cid>,"<host>"`** — resolve hostname. Requires `QIACT` first.
  **"Maximum Response Time 60 s, determined by the network."** Result is a URC:
  `+QIURC: "dnsgip",<err>,<IP_count>,<DNS_ttl>` then one or more
  `+QIURC: "dnsgip",<host_IP_addr>`. On protocol error it returns `ERROR`.
- **Error codes (ch.4):** `564 dns busy`, **`565 dns failed`** (a.k.a. "DNS
  parse failed"). Forum cases of persistent `565` on QIOPEN/QIDNSGIP were
  resolved by modem firmware upgrade, or by using the carrier-assigned DNS
  rather than a hard-coded public resolver.

Net: the BG95 is entirely capable of resolving via the **carrier-assigned DNS**
with no `QIDNSCFG` at all; overriding with `8.8.8.8` is optional, non-persistent,
and on a filtered APN actively harmful.

---

## 4. Vodafone GDSP restricted-APN DNS behavior (web sources)

Sources:
- administrator.de forum "DNS bei Vodafone IoT" (t/676529) — a GDSP user's
  first-hand measurements.
- Vodafone IoT portal help, "APN access lists".
- Cisco Catalyst 9800 "DNS-Based Access Control Lists" config guide — generic
  reference for how FQDN/ACL enforcement is implemented industry-wide (DNS
  snooping / dynamic IP learning).

Findings:

- **The network assigns Vodafone DNS servers at PDP setup.** The GDSP user
  reports always receiving `141.1.1.1` and `195.27.1.1` (Vodafone-owned) from
  the network on the IoT SIM.
- **Third-party / public DNS is filtered.** On the GDSP IoT SIM, forcing
  `nslookup` to use `8.8.8.8` returns **NXDOMAIN for every query**, even when
  `8.8.8.8` itself was added to the ACL. The user concludes "DNS scheint also
  gefiltert zu werden" (DNS appears to be filtered) at the network transition
  (GGSN/PGW). Vodafone GDSP sits in front of the Internet path
  (`Vodafone GDSP =🚫=> Internet => Vodafone DNS`).
- **Filter order: DNS filters are evaluated BEFORE IP ACLs.** "DNS-Filter werden
  vor dem IP-Filter ausgewertet." So even an IP that is allowlisted at L3 cannot
  be reached if reaching it first requires a DNS lookup that the DNS filter
  blocks. After the user entered **all required FQDNs** into the tenant filter,
  reachability worked.
- **FQDN-type ACLs are enforced by DNS snooping / dynamic IP learning.** The
  packet core (or an inline AP/controller analog) snoops the device's DNS
  responses, parses the resolved IP for an allowlisted FQDN, and dynamically
  adds that IP to the permitted set. This is exactly the Cisco "DNS-based ACL /
  walled garden" mechanism: the AP "learns the IP address of the resolved domain
  name from the DNS response … and adds the IP address to the allowed list."
  **This only works if the device resolves through the carrier DNS that the
  snooper can see.** If the device sends DNS to `8.8.8.8` (which the filter
  drops/NXDOMAINs), the snooper never learns the IP, and the subsequent
  connection to the resolved IP is *also* blocked.

Implication for our `*.*` wildcard: a wildcard FQDN in the ACL can only allow
IPs that the DNS snooper actually observes being resolved. If DNS is filtered
before the ACL and/or the snooper only watches the carrier DNS path, the device
must (a) resolve via the **carrier-assigned DNS**, and (b) have its queries
answered (not NXDOMAIN'd) for the snoop→learn→allow chain to fire.

---

## 5. Ranked hypotheses for OUR failure

The observed symptom set is: with `GDNS=1` and a hostname `SERVADDR`, Vodafone
stalls ~60 s then uses BKDNS; Síminn resolves quickly.

> **Important framing correction (from §1):** the "~1.5 s Síminn DNS success"
> seen today was the **BKDNS static print under `GDNS=0`**, not a real QIDNSGIP
> lookup — today both SIMs ran `GDNS=0`. The genuine DNS-path difference
> (`GDNS=1`) is: on Síminn an on-air QIDNSGIP succeeds, on Vodafone it does not
> return a usable result within our window. The hypotheses below are about that
> `GDNS=1` path.

### Hypothesis A — 8.8.8.8 is ACL/DNS-filtered → stall → BKDNS. **(weakened as the *mechanism*, see note)**
- *For:* Vodafone GDSP filters/NXDOMAINs 8.8.8.8 (§4). If the modem used Google
  DNS, queries would die.
- *Against / nuance:* **Our firmware never sends QIDNSCFG**, so the modem is
  *not* using 8.8.8.8 for QIDNSGIP — it uses the carrier-assigned DNS (§2, §3).
  So "8.8.8.8 blocked" cannot be the literal cause of a QIDNSGIP stall *unless*
  the modem had a leftover QIDNSCFG from a prior session (QIDNSCFG is
  non-persistent, so only within the same PDP lifetime). The `DNSCFG` EEPROM
  value is effectively inert today.
- *Settle-it bench command:* after `AT+QIACT=1`, query the active DNS with
  **`AT+QIDNSCFG=1`** (read). If it returns `8.8.8.8/8.8.4.4`, something set it;
  if it returns the carrier IPs (e.g. Vodafone `141.1.1.1`/`195.27.1.1`), the
  Google-DNS hypothesis is dead.

### Hypothesis B — carrier DNS is required but the path still fails on Vodafone. **(strong)**
- *For:* Even using carrier DNS, Vodafone's DNS filter (evaluated before the IP
  ACL, §4) may NXDOMAIN `vakt.systemat.is` until the FQDN is allowlisted *and*
  the wildcard has propagated. The DNS filter sits at the network transition and
  can refuse the lookup outright → modem returns `+QIURC: "dnsgip",565`
  (dns failed) or nothing within 60 s → our fallback.
- *For:* Matches the A/B doc's independent finding that Vodafone also TCP-resets
  the *already-allowed* vakt IP — consistent with an over-restrictive,
  still-converging ACL/DNS policy on `IDER_ACL`.
- *Settle-it bench command:* with the PDP up, run
  **`AT+QIDNSGIP=1,"vakt.systemat.is"`** and capture the raw URC. `565` → carrier
  DNS refused (supports B). A correct IP → DNS itself is fine (points to D).

### Hypothesis C — DNS interception exists but is slow/broken. **(possible, lower)**
- *For:* "60 s, determined by the network" (§3) — on a restricted APN the
  resolver may be slow to answer, or the snoop→learn step adds latency.
- *Against:* The A/B doc shows the *allowed* path is fast (~300 ms TCP reset),
  so the core isn't generally sluggish; a slow-but-correct DNS would still
  occasionally succeed within 60 s, which we don't observe.
- *Settle-it:* same `AT+QIDNSGIP` as B, but timestamp the URC arrival. A late
  (but <60 s) valid answer supports C; a `565`/silence supports B/D.

### Hypothesis D — our firmware's QIDNSGIP URC parser never matches the BG95's real URC. **(strong, firmware-side)**
- *For:* `resolve_host()` only strstr's `"+QIDNSGIP: 1,"` / `"QIDNSGIP: 1,"`,
  but the BG95 emits **`+QIURC: "dnsgip",...`** (§3). On a modem that speaks the
  `+QIURC: "dnsgip"` dialect, our parser would **never** see a match, wait the
  full 2×30 s, and fall back to BKDNS — **a ~60 s stall on any SIM**, reported
  as a DNS failure even when the network resolved the name correctly.
- *Nuance:* Some Quectel firmware/families do emit `+QIDNSGIP: <cid>,<err>...`
  (our MODEM.md §5 marks the exact URC shape "INFERRED"). Which dialect our
  BG95-M2 produces is **unverified** and is itself a settle-it item. If our
  modem emits `+QIDNSGIP: 1,...`, D collapses and the stall is genuinely the
  network (B). If it emits `+QIURC: "dnsgip",...`, D is a real, fixable bug that
  would *also* explain why Síminn "worked" only via the GDNS=0 BKDNS shortcut.
- *Settle-it:* on Síminn with `GDNS=1`, `AT+DEBUG=1`, watch the **raw modem RX**
  for the literal URC after `AT+QIDNSGIP=1,"vakt.systemat.is"`. `+QIURC:
  "dnsgip"` → D confirmed (parser bug). `+QIDNSGIP: 1,0,...` → parser fine.

### Ranking
1. **D** (firmware parser/URC mismatch) — must be ruled out first because it can
   *masquerade* as a network DNS failure on every SIM and is cheap to fix.
2. **B** (carrier-DNS path still filtered on Vodafone) — the most likely true
   *network* cause once D is excluded.
3. **C** (slow interception) — only if B shows a late-but-valid answer.
4. **A** (8.8.8.8 blocked) — true of Vodafone in general but largely moot today
   because we never push QIDNSCFG; relevant only if we start honoring DNSCFG.

---

## 6. Concrete fix recommendations

### 6.1 Config change most likely to fix Vodafone DNS (no firmware change)

**Use the carrier-assigned DNS and a hostname the ACL/snooper can learn.**

1. Ensure the modem is **not** carrying a stale Google-DNS override (it isn't —
   we never send QIDNSCFG; and QIDNSCFG is non-persistent anyway). Optionally
   verify/clear on the bench:
   - `AT+QIDNSCFG=1` → read current DNS for context 1.
   - If it shows `8.8.8.8`, the modem will prefer Google; since we don't set it,
     a fresh `AT+QIACT=1` should repopulate from the network.
2. Keep `GDNS=1` so the device performs a **real** QIDNSGIP through the carrier
   DNS (this is what lets Vodafone's DNS snooper learn the vakt IP for the ACL).
3. Make sure `vakt.systemat.is` (or the production FQDN) is explicitly in the
   `IDER_ACL` **as an FQDN**, not only as an IP — because GDSP evaluates the DNS
   filter before the IP ACL (§4). The `*.*` wildcard should cover this once
   propagated, but an explicit FQDN entry is the deterministic form.

Our console already accepts these (console.c `set_dnscfg` / `U8("GDNS",...)`):
- `AT+GDNS=1` → enable the real resolution path.
- `AT+DNSCFG="141.1.1.1","195.27.1.1"` → if we ever *do* wire DNSCFG to the
  modem, it must point at the **carrier** DNS, not Google. (Today this value is
  stored but unused.)

### 6.2 Firmware changes (ps-cb-openfw) — recommended regardless

1. **Fix the QIDNSGIP URC parser (Hypothesis D).** Accept **both** dialects:
   - `+QIURC: "dnsgip",<err>,...` and `+QIURC: "dnsgip",<ip>` (per the BG95
     manual), and
   - `+QIDNSGIP: 1,<err>...` / `+QIDNSGIP: 1,"<ip>"` (legacy/INFERRED shape).
   Parse `<err>` first; only treat `err==0` with a following IP as success; treat
   `564/565` as immediate failure (break the retry, go to BKDNS) instead of
   waiting the full window.
2. **Fail fast on a definitive DNS error.** If the URC reports `565 dns failed`,
   don't burn the second 30 s try — go straight to BKDNS. This turns a 60 s
   stall into a sub-5 s fallback (matching stock's ~3–4.5 s fail cadence, §1b).
3. **Decide the DNSCFG policy explicitly.** Either:
   - (preferred for restricted APNs) **never** send `AT+QIDNSCFG`, always use
     the PDP-assigned carrier DNS — and stop printing/accepting Google DNS as a
     meaningful default (change the `dns1/dns2` factory defaults away from
     8.8.8.8/8.8.4.4 to avoid implying they take effect), or
   - if `DNSCFG` is meant to be honored, actually send
     `AT+QIDNSCFG=1,<dns1>,<dns2>` **after** `AT+QIACT=1`, and default it to the
     carrier DNS learned from `AT+CGCONTRDP`/`AT+QIDNSCFG=1` rather than Google.

### 6.3 Exact bench command sequence to settle it (read-only, no flashing)

With the Vodafone SIM, PDP up, `AT+DEBUG=1`, `GDNS=1`:

```text
AT+CGCONTRDP            # PDP-assigned DNS (primary/secondary) — capture these IPs
AT+QIDNSCFG=1           # read active DNS for context 1 (Google? carrier?)
AT+QIDNSGIP=1,"vakt.systemat.is"   # capture the RAW URC + timing
AT+QIDNSGIP=1,"google.com"         # control: does a benign name resolve?
```

Interpretation:
- `+QIURC: "dnsgip",0,...` + IP → DNS works via carrier DNS; our stall was the
  parser (Hypothesis D). Fix firmware parser.
- `+QIURC: "dnsgip",565` for vakt but `0` for google.com → Vodafone DNS filter
  blocking the FQDN (Hypothesis B) → escalate FQDN allowlisting on `IDER_ACL`.
- `AT+QIDNSCFG=1` returning `8.8.8.8` → a stale override exists; clear it and
  re-`QIACT`.

### 6.4 Dependency on the wildcard bench worker

The wildcard worker is capturing **`AT+CGCONTRDP`** today. That output gives the
**carrier-assigned DNS server IPs** for the Vodafone PDP context. Once available:
- record them here,
- if we choose to honor `DNSCFG`, set `AT+DNSCFG` to those carrier IPs (not
  8.8.8.8) for the Vodafone profile,
- confirm whether the wildcard `*.*` FQDN entry caused the DNS snooper to start
  allowing resolved vakt IPs (re-run §6.3 after propagation).

---

## 7. One-paragraph root-cause statement

On the Vodafone GDSP restricted APN, DNS is filtered at the packet core and the
filter is evaluated **before** the IP ACL; FQDN allowlisting works by the network
snooping the device's DNS (via the **carrier-assigned** DNS) to learn and permit
the resolved IP. Our open firmware, when `GDNS=1`, issues `AT+QIDNSGIP` but (a)
its URC parser only matches `+QIDNSGIP: 1,...` and may never match the BG95's
actual `+QIURC: "dnsgip",...` result, and (b) waits a fixed 2×30 s before
falling back to BKDNS — producing the observed ~60 s stall. The stored
`DNSCFG=8.8.8.8` is a red herring today (never sent to the modem; QIDNSCFG is
non-persistent), but if honored it would make things worse because Vodafone
NXDOMAINs Google DNS. The single most likely *config* fix is to resolve through
the carrier DNS with the target FQDN explicitly allowlisted; the single most
likely *firmware* fix is to parse the `+QIURC: "dnsgip"` URC and fail fast on
`565`.
