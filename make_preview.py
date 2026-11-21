"""Generate a standalone Forkit sample with the timeline and chart ready to explore."""

import argparse
import json
from pathlib import Path

from app import Inventory, ROOT


def sample_state():
    inventory = Inventory(":memory:", True)
    try:
        state = inventory.discover()
        for tool in state["tools"]:
            inventory.mutate("register", {"key": tool["key"]})
        editor = next(tool for tool in state["tools"] if tool["metadata"]["source"] == "editor")
        inventory.mutate("link", {"key": editor["key"], "event": "evt-104", "consent": True})
        inventory.mutate("incident", {"event": "evt-106"})
        return inventory.refresh_timeline()
    finally:
        inventory.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-state", type=Path, help="Saved state from an isolated synthetic-workspace pilot")
    args = parser.parse_args()
    state = json.loads(args.pilot_state.read_bytes()) if args.pilot_state else sample_state()
    page = (ROOT / "index.html").read_text(encoding="utf-8-sig")
    data = json.dumps(state).replace("<", r"\u003c")
    marker = "const offlinePreview = null; // Replaced only in the standalone preview."
    if page.count(marker) != 1:
        raise RuntimeError("The standalone preview marker is missing or ambiguous.")
    page = page.replace(marker, f"const offlinePreview = {data};").replace("__BASE__", "")
    filename = "Forkit Pilot Preview.html" if args.pilot_state else "Forkit Preview.html"
    destination = ROOT / filename
    destination.write_text(page, encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
