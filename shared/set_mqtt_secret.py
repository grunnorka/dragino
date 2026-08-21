#!/usr/bin/env python3
"""Ask for the Railway MQTT password on screen, verify it, save it locally.

The password is typed into a masked desktop dialog, never into a chat or a
shell argument, and is written only to railway-mqtt.local.env (gitignored).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))

import prompt_user  # noqa: E402

TARGET = ROOT / "railway-mqtt.local.env"
HOST = "altaria.proxy.rlwy.net"
FALLBACK_IP = "66.33.22.220"
PORT = 33239
USER = "dragino"
ATTEMPTS = 3


def ask_password(attempt: int) -> str | None:
    if not shutil.which("zenity"):
        import getpass

        return getpass.getpass(f"Railway MQTT password for '{USER}': ")
    suffix = "" if attempt == 1 else f"  (attempt {attempt} of {ATTEMPTS})"
    proc = subprocess.run(
        [
            "zenity",
            "--password",
            "--title=Railway MQTT password needed" + suffix,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def verify(password: str) -> tuple[bool, str]:
    import paho.mqtt.client as mqtt

    try:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="dragino-cred-check"
        )
    except AttributeError:  # paho 1.x
        client = mqtt.Client(client_id="dragino-cred-check")
    client.username_pw_set(USER, password)
    try:
        client.connect(HOST, PORT, keepalive=10)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    client.loop_start()
    import time

    for _ in range(100):
        if client.is_connected():
            client.loop_stop()
            client.disconnect()
            return True, "connected and authenticated"
        time.sleep(0.1)
    client.loop_stop()
    return False, "no CONNACK within 10 s (wrong username/password?)"


def main() -> None:
    prompt_user.info(
        "Broker password needed",
        [
            f"A masked password box is opening for MQTT user '{USER}'",
            f"on {HOST}:{PORT}.",
            "",
            "It will be verified against the broker and saved only to",
            "railway-mqtt.local.env, which is gitignored.",
        ],
    )
    for attempt in range(1, ATTEMPTS + 1):
        password = ask_password(attempt)
        if password is None:
            raise SystemExit("Cancelled - no password entered.")
        if not password:
            print("Empty password, try again.", flush=True)
            continue
        print(f"Verifying against {HOST}:{PORT} ...", flush=True)
        ok, detail = verify(password)
        print(f"  {detail}", flush=True)
        if ok:
            TARGET.write_text(
                "\n".join(
                    [
                        "# Railway MQTT secrets. Gitignored - do not commit.",
                        f"MQTT_HOST={HOST}",
                        f"MQTT_PORT={PORT}",
                        f"MQTT_USER={USER}",
                        f"MQTT_PASS={password}",
                        f"MQTT_FALLBACK_IP={FALLBACK_IP}",
                        "MQTT_APP_PORT=1883",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            TARGET.chmod(0o600)
            print(f"SAVED {TARGET}", flush=True)
            return
        print("  rejected - asking again", flush=True)
    raise SystemExit("Could not authenticate after all attempts.")


if __name__ == "__main__":
    main()
