# Vodafone connectivity for Dragino PS-CB-NA

SIM, APN, and operator ACL notes for Vodafone GDSP roaming, Vodafone Iceland / Sýn, and Síminn.

---

## GDSP API audit — blocked

A read-only audit of the Vodafone GDSP portal at `https://iotportal.vodafone.com` was attempted on 2026-08-17 for the Ider account.

**Result: authentication failed.**

- The portal is a WSO2 Carbon / OAuth2 platform, not a Jasper-style Basic Auth API.
- Portal username/password (`cursorAgent`) returned `401 Unauthorized` on every probed endpoint (`/api/v1/devices`, `/rws/api/v1/devices`, `/api/v1/sessionHistory`, etc.).
- An API key or OAuth client credentials are required to proceed.

To retry the audit, provide one of:

- **Option A:** A Jasper-style API key for `cursorAgent`, then use `Authorization: Basic base64(cursorAgent:apiKey)`.
- **Option B:** OAuth2 `client_id` + `client_secret` registered in the portal, plus permission to make the single `POST /oauth2/token` call.
- **Option C:** A pre-generated Bearer token.

Until then, no SIM inventory, rate-plan diff, or session history can be retrieved via API.

---

## IDER_ACL — only `167.235.104.181` is allowed

The Vodafone IoT portal shows an Internet Access Control List named `IDER_ACL` for APN `lpwa.vodafone.io`:

| Field | Value |
|---|---|
| Name | `IDER_ACL` |
| APN ACL ID | `1090680` |
| APN | `lpwa.vodafone.io` |
| Organisation | Ider |
| Parent | VFIS |

**Allow-list members:** one IPv4 address `167.235.104.181` (all ports).

Practical effect for devices on `lpwa.vodafone.io`:

- They can reach `vakt.systemat.is` (`167.235.104.181:1883`).
- They **cannot** reach the Railway MQTT proxy (`66.33.22.220:33239`) or `broker.hivemq.com` unless those IPs are added to `IDER_ACL`.
- If the device must talk to Railway, ask the portal admin to add `66.33.22.220` (or `altaria.proxy.rlwy.net`) to the ACL.

---

## APN by SIM type

Read `AT+IMSI=?` after unlocking to decide. The IMSI prefix determines which APN to use.

| SIM | IMSI prefix | AT command | Notes |
|---|---|---|---|
| **Vodafone GDSP** (global IoT roaming) | `90128` | `AT+APN=NULL` | Network supplies APN; do **not** use `lpwa.vodafone.is` |
| **Síminn** | `27401` | `AT+APN=internet` | Standard consumer/IoT APN |
| **Vodafone Iceland / Sýn** | `27402` | `AT+APN=lpwa.vodafone.is` | Local operator APN |

If in doubt with a Vodafone-branded SIM, prefer `AT+APN=NULL` first and let the network reject it. A wrong explicit APN such as `lpwa.vodafone.is` on a GDSP SIM will fail PDP activation.

---

## Sources

- `docs/LLM_SENSOR_SETUP_MANUAL.md` §6.4
