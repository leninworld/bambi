"""Run pre-commit on exactly the reconstructed upstream file set."""
import json
from pathlib import Path
import subprocess
import sys

if sys.flags.optimize:
    raise SystemExit("OPTIMIZE_FORBIDDEN")
sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent


def upstream_paths():
    rows = json.loads((HERE / "source-manifest.json").read_text())
    paths = [row["path"] for row in rows]
    if len(paths) != 246 or len(set(paths)) != 246:
        raise SystemExit("HOOK_SOURCE_SET")
    if any(path.startswith("validation/") or path.startswith(".github/workflows/validate-bambi1097-")
           for path in paths):
        raise SystemExit("HOOK_SCOPE")
    return paths


def command(environment):
    return ["pixi", "run", "-e", environment, "pre-commit", "run", "--files",
            *upstream_paths(), "--show-diff-on-failure"]


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"py312", "py313"}:
        raise SystemExit("usage: run_upstream_hooks.py py312|py313")
    raise SystemExit(subprocess.run(command(sys.argv[1]), stdin=subprocess.DEVNULL).returncode)


if __name__ == "__main__":
    main()
