#!/usr/bin/env python3
"""Scan local gateway/hosts for open ports useful for MQTT front."""
import socket

hosts = [
    "192.168.3.1",
    "192.168.2.240",
    "192.168.10.250",
    "192.168.2.174",
    "192.168.2.1",
    "192.168.10.1",
]
ports = [22, 80, 443, 1883, 8080, 8443, 9000]
for h in hosts:
    for p in ports:
        s = socket.socket()
        s.settimeout(0.8)
        try:
            if s.connect_ex((h, p)) == 0:
                print(f"OPEN {h}:{p}", flush=True)
        except OSError as e:
            print(f"ERR {h}:{p} {e}", flush=True)
        finally:
            s.close()
print("scan done", flush=True)
