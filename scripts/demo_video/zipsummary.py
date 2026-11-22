"""JSON summary of an audit ZIP (name, files, checksum result) for the projector cut."""

import hashlib
import json
import sys
import zipfile

z = zipfile.ZipFile(sys.argv[1])
names = z.namelist()
ok = total = 0
sums = next((n for n in names if n.upper().endswith("SHA256SUMS")), None)
if sums:
    for line in z.read(sums).decode().splitlines():
        if line.strip():
            digest, name = line.split(None, 1)
            total += 1
            ok += hashlib.sha256(z.read(name.strip())).hexdigest() == digest
print(json.dumps({"name": "audit-report.zip",
                  "files": [n.split("/")[-1] for n in names], "ok": ok, "total": total}))
