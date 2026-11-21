"""Open the VM's audit dashboard after verifying its local SSH tunnel."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

def dashboard_state(opener, address):
    try:
        with opener.open(address + "/api/state", timeout=2) as response:
            state = json.load(response)
        return isinstance(state, dict) and state
    except (OSError, ValueError, URLError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify access without opening a browser")
    parser.add_argument("--pilot", action="store_true", help="Open the isolated agent workflow pilot")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name(".forkit-vm.json"),
                        help="Local VM connection settings")
    options = parser.parse_args()
    try:
        config = json.loads(options.config.read_text(encoding="utf-8-sig"))
        for field in ("instance", "project", "zone", "account"):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@+-]*", config[field]):
                raise ValueError("Invalid VM connection setting.")
        if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", config["host_key"]):
            raise ValueError("Expected the verified SHA256 SSH host fingerprint.")
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f"Could not load VM connection settings: {error}\n")
    port = 18766 if options.pilot else 18765
    address = f"http://127.0.0.1:{port}"
    page = address + ("/#timeline-view" if options.pilot else "/#archive-view")
    portable = Path(os.path.expandvars(config.get("gcloud_python", "")))
    installed = shutil.which("gcloud")
    if portable.is_file():
        cli = [sys.executable, str(portable)]
    elif installed:
        cli = [installed]
    else:
        parser.exit(1, "Google Cloud CLI was not found. Install it or restore the portable copy.\n")
    opener = build_opener(ProxyHandler({}))
    if dashboard_state(opener, address):
        parser.exit(1, f"Port {port} is already serving a dashboard. Close the previous tunnel and retry.\n")
    command = cli + [
        "compute", "ssh", config["instance"], f"--project={config['project']}",
        f"--zone={config['zone']}", f"--account={config['account']}", "--tunnel-through-iap",
        "--quiet", "--ssh-key-expire-after=2h", "--ssh-flag=-batch", "--ssh-flag=-hostkey",
        f"--ssh-flag={config['host_key']}",
        "--ssh-flag=-L", f"--ssh-flag=127.0.0.1:{port}:127.0.0.1:{port}",
        "--command=sleep 45" if options.check else "--command=sleep 43200",
    ]
    print("Connecting to the Forkit VM...", flush=True)
    try:
        with subprocess.Popen(command) as tunnel:
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline and tunnel.poll() is None:
                    state = dashboard_state(opener, address)
                    if state and state.get("demo") is False and state.get("archive"):
                        print(f'Connected. {state["archive"]["total"]} audit records available.', flush=True)
                        print(page, flush=True)
                        if options.check:
                            return
                        webbrowser.open(page)
                        print("Keep this window open. Press Ctrl+C to disconnect.", flush=True)
                        tunnel.wait()
                        return
                    time.sleep(.4)
                parser.exit(1, "The SSH tunnel did not reach the VM dashboard. Check the connection output above.\n")
            finally:
                if tunnel.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(tunnel.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except KeyboardInterrupt:
        print("Disconnected.")


if __name__ == "__main__":
    main()
