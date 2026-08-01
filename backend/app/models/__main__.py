"""Inspect model roles and the VM inventory:  python -m app.models"""

import asyncio

from dotenv import load_dotenv

load_dotenv()

from .ollama import BASE_URL, KEEP_ALIVE, list_available, resident, validate
from .registry import get_specs


async def main() -> None:
    print(f"Ollama VM : {BASE_URL}")
    print(f"keep_alive: {KEEP_ALIVE}\n")

    available = await list_available()
    usable = await validate()

    print("configured roles:")
    for spec in get_specs():
        actual = usable.get(spec.role, spec.tag)
        if not available:
            state = "VM unreachable"
        elif actual != spec.tag:
            state = f"MISSING -> falls back to {actual}"
        else:
            state = "ok"
        print(f"  {spec.role:<8} {spec.tag:<34} {state}")
        if spec.description:
            print(f"           {spec.description}")

    print(f"\navailable on the VM ({len(available)}):")
    for tag in available:
        print(f"  {tag}")

    loaded = await resident()
    print(f"\nresident in memory ({len(loaded)}):")
    if not loaded:
        print("  none - the next call pays a cold load")
    for m in loaded:
        print(f"  {m.tag:<34} {m.size_gb:.1f} GB  expires {m.expires_at[:19]}")


asyncio.run(main())
