"""Bounded command, identity, receipt and evidence helpers (Bambi #558 attempt 3).

Changes from the attempt-2 helper (9241f420...), each covered by selftest_bounded.py with fakes:
- refuses `python -O` at import (asserts are load-bearing);
- `command()` takes an absolute deadline as well as a duration, re-checks the output cap and the
  log size after the child exits, and kills/reaps the whole process group on every path, including
  normal exit (stragglers that closed the pipe);
- `scan_tree()` counts nested regular files recursively, rejects symlinks/special files, and stops at
  a file-count, byte or deadline bound; `stage_upload()` uses it;
- `save()` always writes strict RFC JSON (allow_nan=False, non-finite values and cycles reduced
  structurally, re-validated by `strict_loads()`) atomically, and structurally reduces an oversized body;
- `ByteBudgetWriter` caps archive bytes;
- `multiset()` / `identity_report()` compare test identities as multisets and never collapse repeats.
"""
import sys
if sys.flags.optimize:
    raise SystemExit("OPTIMIZE_FORBIDDEN")
import collections
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import time


RECEIPT_CAP = 1024**2


class LimitAbort(BaseException):
    """A global stop that independent diagnostic lanes must never catch."""


class TreeLimit(Exception):
    """An evidence tree crossed a file-count, byte, type or deadline bound."""


def limit(condition, message):
    if not condition:
        raise LimitAbort(message)


def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _kill_group(pid):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        if sig == signal.SIGTERM:
            time.sleep(.2)


def command(args, seconds, cap, guard=lambda: None, expected=0, cwd=None, log=None, result=None,
            deadline=None):
    """Bound output, elapsed time and absolute deadline; reap the owned process group on every path."""
    guard()
    if deadline is not None:
        seconds = min(seconds, deadline - time.time())
    if seconds <= 0:
        raise TimeoutError("command deadline already passed")
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACTIONS_")
           and not any(s in k.upper() for s in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY"))}
    sink = Path(log).open('wb') if log is not None else None
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True, env=env, cwd=cwd)
    except BaseException:
        if sink is not None:
            sink.close()
        raise
    sel = selectors.DefaultSelector()
    sel.register(p.stdout, selectors.EVENT_READ)
    data = bytearray()
    start = time.monotonic()
    try:
        while sel.get_map():
            guard()
            if time.monotonic() - start >= seconds:
                raise TimeoutError("command elapsed limit")
            for key, _ in sel.select(.2):
                part = os.read(key.fd, 65536)
                if not part:
                    sel.unregister(key.fileobj)
                    continue
                if len(data) + len(part) > cap:
                    raise OverflowError("command output limit")
                data.extend(part)
                if sink is not None:
                    sink.write(part)
                    sink.flush()
        code = p.wait(timeout=max(.1, min(2, seconds - (time.monotonic() - start))))
        # Post-exit checks: the bytes captured and the bytes on disk must both be under the cap.
        if len(data) > cap or (log is not None and Path(log).stat().st_size > cap):
            raise OverflowError("command output limit after exit")
        if result is not None:
            result['returncode'] = code
        accepted = (expected,) if isinstance(expected, int) else expected
        if code not in accepted:
            raise RuntimeError(f"command exit {code}, expected {expected}: " + bytes(data[-4096:]).decode(errors="replace"))
        return bytes(data)
    finally:
        sel.close()
        _kill_group(p.pid)  # also removes stragglers after a normal exit
        if p.poll() is None:
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=2)
        p.stdout.close()
        if sink is not None:
            sink.close()


def scan_tree(root, max_files, max_bytes, deadline=None):
    """Recursive bounded scan. Stops at the first bound; rejects symlinks and special files."""
    files = total = 0
    stack = [Path(root)]
    while stack:
        if deadline is not None and time.time() >= deadline:
            raise TreeLimit("TREE_SCAN_DEADLINE")
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    raise TreeLimit("TREE_SYMLINK " + entry.path)
                if stat.S_ISDIR(mode):
                    stack.append(Path(entry.path))
                    continue
                if not stat.S_ISREG(mode):
                    raise TreeLimit("TREE_SPECIAL_FILE " + entry.path)
                files += 1
                total += entry.stat(follow_symlinks=False).st_size
                if files > max_files:
                    raise TreeLimit("TREE_FILE_COUNT")
                if total > max_bytes:
                    raise TreeLimit("TREE_BYTES")
    return {"files": files, "bytes": total}


def _reject_constant(name):
    raise ValueError("NON_STANDARD_JSON_CONSTANT " + name)


def strict_loads(text):
    """RFC 8259 JSON only: NaN, Infinity and -Infinity are rejected."""
    return json.loads(text, parse_constant=_reject_constant)


def sanitize(value, _ancestors=None):
    """Structural, JSON-safe copy: non-finite floats and cycles become explicit markers, keys become strings."""
    ancestors = _ancestors or set()
    if isinstance(value, float):
        return value if math.isfinite(value) else {"non_finite": repr(value)}
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, (dict, list, tuple, set, frozenset)):
        if id(value) in ancestors:
            return {"circular_reference": True}
        inner = ancestors | {id(value)}
        if isinstance(value, dict):
            return {str(k): sanitize(v, inner) for k, v in value.items()}
        items = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value
        return [sanitize(v, inner) for v in items]
    return {"unserializable_type": type(value).__name__, "repr": repr(value)[:2000]}


def dumps_strict(value, **kwargs):
    return json.dumps(sanitize(value), allow_nan=False, **kwargs)


def _reduce(value, full_bytes, digest):
    if isinstance(value, dict):
        keep = {k: value[k] for k in ("status", "phase", "lane", "first_failure", "ok", "attempt")
                if k in value and len(dumps_strict(value[k])) <= 16384}
        keep["keys"] = sorted(map(str, value))[:200]
    else:
        keep = {"type": type(value).__name__}
    keep.update(reduced=True, full_bytes=full_bytes, full_sha256=digest)
    return keep


def save(path, value, cap=RECEIPT_CAP):
    """Always write strict, valid JSON atomically. Oversized bodies are reduced structurally, never sliced."""
    try:
        text = dumps_strict(value, indent=2, sort_keys=True) + "\n"
    except (TypeError, ValueError, RecursionError) as exc:
        text = dumps_strict({"reduced": True, "serialization_error": type(exc).__name__}) + "\n"
    if len(text.encode()) > cap:
        raw = text.encode()
        text = dumps_strict(_reduce(value, len(raw), hashlib.sha256(raw).hexdigest()), indent=2, sort_keys=True) + "\n"
    strict_loads(text)  # validate before the atomic replace
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


class ByteBudgetWriter:
    """File-like sink that never writes bytes beyond its remaining evidence budget."""

    def __init__(self, stream, cap):
        self.stream, self.cap, self.written = stream, cap, 0

    def write(self, data):
        if self.written + len(data) > self.cap:
            raise OverflowError("compressed evidence budget")
        n = self.stream.write(data)
        self.written += n
        return n

    def flush(self):
        if hasattr(self.stream, "flush"):
            self.stream.flush()


def multiset(items):
    return collections.Counter(items)


def identity_report(collected, reports):
    """Multiset test-identity accounting. Duplicates are reported, never collapsed into a set."""
    counts = multiset(collected)
    duplicates = {k: v for k, v in counts.items() if v > 1}
    phases = multiset((r['nodeid'], r['when']) for r in reports)
    missing_teardown = sorted(k for k, v in counts.items() if phases[(k, 'teardown')] != v)
    extra = sorted({r['nodeid'] for r in reports} - set(counts))
    outcomes = multiset((r['when'], r['outcome']) for r in reports)
    skipped = sorted({r['nodeid'] for r in reports if r['outcome'] == 'skipped'})
    failed = sorted({r['nodeid'] for r in reports if r['outcome'] == 'failed'})
    return {"collected_occurrences": sum(counts.values()), "unique": len(counts),
            "duplicates": duplicates, "missing_or_mismatched_teardown": missing_teardown,
            "unexpected_report_ids": extra, "outcomes": {f"{w}:{o}": n for (w, o), n in sorted(outcomes.items())},
            "skipped": skipped, "failed": failed,
            "complete": not duplicates and not missing_teardown and not extra}


def stage_upload(source, destination, cap, deadline=None, max_files=4000):
    """Preserve original evidence, stage only a capped upload; oversize falls back to an allowlist."""
    source, destination = Path(source), Path(destination)
    destination.mkdir(exist_ok=False)
    try:
        size = scan_tree(source, max_files, 16 * cap, deadline)["bytes"]
        scan_error = None
    except TreeLimit as exc:
        size, scan_error = None, str(exc)
    cap_hit = size is None or size + 64 * 1024 > cap
    allowed = {'summary.json', 'first-failure.json', 'host-admission.json', 'identity.json',
               'inside/lock-identity.json', 'inside/baseline-collection-result.json',
               'inside/candidate-test-results.json', 'inside/identity-report.json',
               'inside/docs-comparison.json', 'inside/mutation-sensitivity.json',
               'inside/early-fallback-result.json', 'inside/early-hashseed-result.json'}
    total = files = 0
    for path in sorted(p for p in source.rglob('*') if p.is_file() and not p.is_symlink()):
        if deadline is not None and time.time() >= deadline:
            scan_error = scan_error or "STAGE_DEADLINE"
            break
        name = path.relative_to(source).as_posix()
        if cap_hit and name not in allowed:
            continue
        length = path.stat().st_size
        if total + length + 64 * 1024 > cap or files >= max_files:
            continue
        target = destination / name
        target.parent.mkdir(exist_ok=True, parents=True)
        shutil.copyfile(path, target)
        total += length
        files += 1
    row = {'cap_hit': cap_hit, 'original_bytes': size, 'scan_error': scan_error,
           'staged_bytes_before_receipt': total, 'staged_files': files, 'cap': cap,
           'original_evidence_preserved': True,
           'status': 'CAP_HIT_ALLOWLIST_ONLY_NOT_FULL_PASS' if cap_hit or scan_error else 'FULL_BOUNDED_EVIDENCE'}
    save(destination / 'upload-cap-receipt.json', row, cap=64 * 1024)
    final = scan_tree(destination, max_files + 1, cap)
    if final["bytes"] > cap:
        raise OverflowError("UPLOAD_OVER_CAP")
    return row
