#!/usr/bin/env python3
import urllib.request

ip = "212.30.223.181"
ports = [22, 80, 443, 1883, 8080, 8443, 8000, 4080, 4028]
for p in ports:
    try:
        req = urllib.request.Request(
            "https://ports.yougetsignal.com/check-port.php",
            data=f"remoteAddress={ip}&portNumber={p}".encode(),
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0",
            },
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
        if "flag_green" in body or "is open" in body.lower():
            state = "open"
        elif "flag_red" in body or "is closed" in body.lower():
            state = "closed"
        else:
            state = "unknown"
        print(f"{ip}:{p} {state}", flush=True)
    except Exception as e:
        print(f"{ip}:{p} err {e}", flush=True)
