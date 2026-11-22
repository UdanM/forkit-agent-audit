"""Print an audit ZIP's file list and verify its SHA256SUMS (shown on screen by the recorder)."""

import hashlib
import json
import sys
import zipfile

z = zipfile.ZipFile(sys.argv[1])
names = z.namelist()
lines = [f"{z.getinfo(n).file_size:>7,}  {n}" for n in names]
out = "\n".join(lines[:12]) + (f"\n        … {len(lines) - 12} more files" if len(lines) > 12 else "")
sums = next((n for n in names if n.upper().endswith(("SHA256SUMS", "SHA256SUMS.TXT"))), None)
if sums:
    ok = total = 0
    for line in z.read(sums).decode().splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        total += 1
        ok += hashlib.sha256(z.read(name.strip())).hexdigest() == digest
    out += f"\n\n<span class=ok>SHA256SUMS: {ok}/{total} files verified</span>"
report = next((n for n in names if n.endswith(".json") and "report" in n.lower()), None)
if report:
    rs = json.loads(z.read(report)).get("report_sha256")
    if rs:
        out += f"\nreport_sha256 {rs[:16]}…"
print(out)
