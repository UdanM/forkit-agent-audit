"""Run the fixture journey against the same inventory backend as the web app."""
import tempfile
from pathlib import Path

from app import Inventory


def main():
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "demo.sqlite3"
        inventory = Inventory(database, True)
        tools = inventory.discover()["tools"]
        print("Discovered:", ", ".join(tool["metadata"]["name"] for tool in tools))
        mcp = {tool["metadata"]["source"]: tool["key"] for tool in tools
               if tool["metadata"]["kind"] == "MCP"}
        for key in mcp.values():
            inventory.mutate("register", {"key": key})
        registered = {tool["metadata"]["source"]: tool["id"]
                      for tool in inventory.state()["tools"] if tool["id"]}
        inventory.db.close()

        inventory = Inventory(database, True)
        try:
            for tool in inventory.discover(changed=True)["tools"]:
                source = tool["metadata"]["source"]
                if source in registered:
                    print(f"{source}: {tool['id']}")
                if tool["changes"]:
                    print(f"  Changes: {tool['changes']}")
            for event in inventory.state()["events"]:
                approval = event["approval"] or "Missing"
                print(f"{event['id']} | {event['source']} | {event['timestamp']} | "
                      f"{len(event['candidates'])} candidate(s) | Action approval: {approval}")
            if input("Link evt-104 to the editor tool? [y/N] ").strip().lower() == "y":
                link = inventory.mutate("link", {"event": "evt-104", "key": mcp["editor"],
                                                 "consent": True})["links"]["evt-104"]
                print(f"Event linked to {link['tool_id']}")
            else:
                print("Event remains unlinked.")
        finally:
            inventory.db.close()


if __name__ == "__main__":
    main()
