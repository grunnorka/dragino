"""Quick scan for LTC2 BLE advertise name."""
import asyncio
from bleak import BleakScanner

TARGET = "869181074162403"


async def main() -> None:
    print(f"Scanning 20s for {TARGET} ...", flush=True)
    found = await BleakScanner.discover(timeout=20.0)
    names = sorted({(d.name or "") for d in found if d.name})
    hits = [(d.name, d.address) for d in found if (d.name or "") == TARGET]
    print(f"hits={hits or 'NONE'}", flush=True)
    print(f"named_count={len(names)} sample={names[:50]}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
