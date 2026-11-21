"""Create a bounded, hashed, privacy-scanned artifact staging directory."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

if sys.flags.optimize:
    raise SystemExit("OPTIMIZE_FORBIDDEN")
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
CAP = 64 * 1024 * 1024
FILES = 300
PATTERNS = [
    rb"/Users/", rb"open_source_control_plane", rb"co_create", rb"leninmookiah",
    rb"gh[pousr]_[A-Za-z0-9]{20,}", rb"github_pat_[A-Za-z0-9_]{20,}",
    rb"AKIA[0-9A-Z]{16}", rb"-----BEGIN [A-Z ]*PRIVATE KEY",
    rb"(?i)authorization:\s*(bearer|token)\s+\S{8,}",
]

if sys.argv[1:] == ["--selftest-imports"]:
    print("IMPORTS_PASS stage_evidence")
    raise SystemExit(0)


def need(value, message):
    if not value:
        raise SystemExit("EVIDENCE_STOP " + message)


def main():
    if len(sys.argv) < 4:
        raise SystemExit("usage: stage_evidence.py DEST KIND SOURCE...")
    dest, kind = Path(sys.argv[1]), sys.argv[2]
    need(not dest.exists(), "DEST_EXISTS")
    dest.mkdir(parents=True)
    for raw in sys.argv[3:]:
        source = Path(raw)
        need(source.is_file() and not source.is_symlink(), "SOURCE:" + raw)
        target = dest / source.name
        need(not target.exists(), "DUPLICATE_NAME:" + source.name)
        shutil.copyfile(source, target)
    rows, total, matches = [], 0, []
    for path in sorted(dest.iterdir()):
        mode = path.lstat().st_mode
        need(stat.S_ISREG(mode), "NON_REGULAR:" + path.name)
        data = path.read_bytes()
        total += len(data)
        need(len(rows) < FILES and total <= CAP, "BOUNDS")
        for pattern in PATTERNS:
            if re.search(pattern, data):
                matches.append({"path": path.name, "pattern": pattern.decode()})
        rows.append({"path": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    need(not matches, "PRIVACY")
    manifest = {"kind": kind, "files": rows, "bytes": total, "privacy_patterns": len(PATTERNS),
                "privacy_matches": matches, "status": "PASS"}
    (dest / "evidence-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    need(sum(p.stat().st_size for p in dest.iterdir()) <= CAP, "FINAL_CAP")
    print("EVIDENCE_PASS", kind, len(rows), total)


if __name__ == "__main__":
    main()
