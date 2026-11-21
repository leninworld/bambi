"""Fail-closed hosted-run and reconstructed-source identity guard."""
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

if sys.flags.optimize:
    raise SystemExit("OPTIMIZE_FORBIDDEN")
sys.dont_write_bytecode = True

ROOT = Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
EXPECTED = {
    "GITHUB_REPOSITORY": "leninworld/bambi",
    "GITHUB_REF": "refs/heads/validate-bambi1097-6fb8d132-h4",
    "GITHUB_REF_NAME": "validate-bambi1097-6fb8d132-h4",
    "GITHUB_EVENT_NAME": "push",
    "GITHUB_RUN_ATTEMPT": "1",
}

if sys.argv[1:] == ["--selftest-imports"]:
    print("IMPORTS_PASS verify_identity")
    raise SystemExit(0)


def need(condition, message):
    if not condition:
        raise SystemExit("IDENTITY_STOP " + message)


def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def main():
    for key, expected in EXPECTED.items():
        need(os.environ.get(key) == expected, key)
    need(re.fullmatch(r"[0-9a-f]{40}", os.environ.get("GITHUB_SHA", "")) is not None, "GITHUB_SHA")
    rows = json.loads((HERE / "source-manifest.json").read_text())
    need(len(rows) == 246, "SOURCE_COUNT")
    seen = set()
    for row in rows:
        rel = row["path"]
        need(rel not in seen and ".." not in Path(rel).parts and not Path(rel).is_absolute(), "SOURCE_PATH")
        seen.add(rel)
        path = ROOT / rel
        mode = path.lstat().st_mode
        if row["mode"] == "120000":
            need(stat.S_ISLNK(mode), "SOURCE_SYMLINK:" + rel)
            data = os.readlink(path).encode()
        else:
            need(stat.S_ISREG(mode), "SOURCE_REGULAR:" + rel)
            data = path.read_bytes()
            need(bool(mode & 0o111) == (row["mode"] == "100755"), "SOURCE_MODE:" + rel)
        need(len(data) == row["bytes"], "SOURCE_BYTES:" + rel)
        need(hashlib.sha256(data).hexdigest() == row["sha256"], "SOURCE_SHA256:" + rel)
        need(blob(data) == row["git_blob"], "SOURCE_BLOB:" + rel)
    report = {
        "status": "PASS",
        "source_commit": "6fb8d132d711b030109f0d452ba42c739d655647",
        "source_tree": "fe6848d9100cb8ce639bf731e3377fdb991277e9 with reviewed notebook substitution",
        "files": len(rows),
        "run_sha": os.environ["GITHUB_SHA"],
        "run_id": os.environ["GITHUB_RUN_ID"],
        "run_attempt": 1,
    }
    out = Path(os.environ["RUNNER_TEMP"]) / "bambi1097-identity.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("IDENTITY_PASS", len(rows))


if __name__ == "__main__":
    main()
