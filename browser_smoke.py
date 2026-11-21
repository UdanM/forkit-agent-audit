import json, os, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.request import urlopen

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
    profile = Path(directory) / "browser"
    with subprocess.Popen([sys.executable, "app.py", "--port", "0", "--db",
                           str(Path(directory) / "state.sqlite3"), *sys.argv[2:]],
                          stdout=subprocess.PIPE, text=True) as server:
        try:
            url = server.stdout.readline().split()[-1]
            command = [sys.argv[1], "--headless", "--no-first-run", "--disable-background-networking",
                       "--disable-extensions",
                       "--remote-debugging-port=0", f"--user-data-dir={profile}", url]
            with subprocess.Popen(command, stdout=subprocess.DEVNULL) as browser:
                try:
                    port_file = profile / "DevToolsActivePort"
                    port = None
                    for _ in range(150):
                        try:
                            lines = port_file.read_text().splitlines()
                        except (FileNotFoundError, PermissionError):
                            lines = []
                        if lines:
                            port = lines[0]
                            break
                        if browser.poll() is not None:
                            break
                        time.sleep(.1)
                    if not port:
                        raise RuntimeError(f"Chromium debugger did not start. Browser exit code: {browser.poll()}")
                    endpoint = None
                    for _ in range(50):
                        with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
                            targets = json.load(response)
                        endpoint = next((tab["webSocketDebuggerUrl"] for tab in targets
                                         if tab["url"] == url + "/"), None)
                        if endpoint:
                            break
                        time.sleep(.1)
                    if not endpoint:
                        raise RuntimeError("Chromium did not open the Forkit dashboard.")
                    env = dict(os.environ, FORKIT_TEST_WS=endpoint)
                    subprocess.run(["node", "browser_smoke.mjs"], check=True, env=env, timeout=40)
                finally:
                    browser.terminate()
        finally:
            server.terminate()
