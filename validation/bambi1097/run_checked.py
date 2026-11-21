"""Run one command once with elapsed/output bounds and an atomic JSON receipt."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

if sys.flags.optimize:
    raise SystemExit("OPTIMIZE_FORBIDDEN")
sys.dont_write_bytecode = True
CAP = 16 * 1024 * 1024
SECONDS = 3300


def main():
    if len(sys.argv) < 4 or sys.argv[2] != "--":
        raise SystemExit("usage: run_checked.py RECEIPT -- COMMAND [ARG ...]")
    receipt = Path(sys.argv[1])
    log = receipt.with_suffix(".log")
    if receipt.exists() or log.exists():
        raise SystemExit("ONE_SHOT_OUTPUT_EXISTS")
    command = sys.argv[3:]
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.monotonic()
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True, env=env)
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    digest = hashlib.sha256()
    size = 0
    stop = None
    with log.open("xb") as sink:
        try:
            while sel.get_map():
                if time.monotonic() - started >= SECONDS:
                    stop = "TIMEOUT_NO_RETRY"
                    break
                for key, _ in sel.select(0.25):
                    data = os.read(key.fd, 65536)
                    if not data:
                        sel.unregister(key.fileobj)
                        continue
                    size += len(data)
                    if size > CAP:
                        stop = "OUTPUT_CAP_NO_RETRY"
                        break
                    digest.update(data)
                    sink.write(data)
                if stop:
                    break
        finally:
            sel.close()
            if stop or proc.poll() is None:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(proc.pid, sig)
                    except ProcessLookupError:
                        break
                    time.sleep(0.2)
            proc.wait(timeout=5)
            proc.stdout.close()
    body = {"command": command, "exit": proc.returncode, "stop": stop, "retry": False,
            "seconds": time.monotonic() - started, "log_bytes": min(size, CAP),
            "log_sha256": digest.hexdigest()}
    tmp = receipt.with_suffix(receipt.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(tmp, receipt)
    print(log.read_text(errors="replace")[-8000:])
    if stop or proc.returncode != 0:
        raise SystemExit("COMMAND_FAILED_NO_RETRY")


if __name__ == "__main__":
    main()
