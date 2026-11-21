"""Execute the reviewed causal_effects notebook once, run the private probe, verify outputs.

Script (import-only in the self-test preflight). Run by the attempt-10 workflow with the
environment python: ``python -I -B /packet/notebook_exec.py``. No automatic retry.
Writes the executed public notebook (probe removed) to the source tree and evidence.
"""
import base64
import hashlib
import json
import os
import re
import socket
import sys
import time
from pathlib import Path

if sys.flags.optimize:
    raise SystemExit('A10_REFUSED_PYTHON_O')
sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import nbformat
from nbclient import NotebookClient

from fit_health import check_notebook_outputs

if sys.argv[1:] == ['--selftest-imports']:
    print('IMPORTS_PASS notebook_exec')
    raise SystemExit(0)

P = Path(os.environ.get('A10_PACKET', '/packet'))
W = Path(os.environ.get('A10_WORK', '/work'))
E = W / 'evidence'
SRC = Path(os.environ.get('A10_SOURCE', str(W / 'src')))
PLAN = json.loads((P / 'a10-plan.json').read_text())
NB = PLAN['notebook']
TARGET = SRC / NB['path']
REVIEWED = P / NB['reviewed_file']
PNG = b'\x89PNG\r\n\x1a\n'
STDERR_ALLOWED = [re.compile(p) for p in NB['stderr_allowed']]
SECRET_PATTERNS = [re.compile(p) for p in (
    r'gh[pousr]_[A-Za-z0-9]{20,}', r'github_pat_[A-Za-z0-9_]{20,}', r'AKIA[0-9A-Z]{16}',
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----', r'(?i)(password|passwd|secret|api[_-]?key|token)\s*[=:]\s*\S{6,}',
    r'(?i)authorization:\s*\S+')]


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def text_of(output, mime='text/plain'):
    if output.get('output_type') == 'stream':
        value = output.get('text', '')
    else:
        value = output.get('data', {}).get(mime, '')
    return ''.join(value) if isinstance(value, list) else value


def forbidden_values():
    values = ['/work', '/Users/', '/home/', '/env/', '/packet', '/cache', socket.gethostname()]
    host = os.environ.get('A10_HOST_HOSTNAME', '')
    if host:
        values += [host, host.split('.')[0]]
    values += [v for k, v in os.environ.items()
               if any(s in k.upper() for s in ('TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL', 'API_KEY'))
               and len(v) >= 8]
    return [v for v in values if v]


def probe_source():
    """Return kernel code that imports the private sibling probe under isolation."""
    return "\n".join([
        "import os as _os, sys as _sys",
        "from pathlib import Path as _Path",
        "_helper_dir = str(_Path(_os.environ['A10_PACKET']).resolve())",
        "_sys.path.insert(0, _helper_dir)",
        "import a10_probe",
        "if _os.environ.get('BAMBI1097_SELFTEST_IMPORTS') == '1':",
        "    print('IMPORTS_PASS kernel_a10_probe')",
        "else:",
        "    a10_probe_report = a10_probe.run(globals())",
    ])


def verify(nb, reviewed):
    """Strict public-output contract; returns a JSON-safe report or raises."""
    rep = {}
    check(len(nb.cells) == len(reviewed.cells) == NB['cells'], 'cell count')
    for a, b in zip(reviewed.cells, nb.cells):
        check(a.cell_type == b.cell_type and a.get('id') == b.get('id'), 'cell type/id changed')
        check(a.source == b.source, 'cell source not byte-identical: ' + str(a.get('id')))
    code = [c for c in nb.cells if c.cell_type == 'code']
    counts = [c.execution_count for c in code]
    check(counts == list(range(1, len(code) + 1)), 'execution counts not sequential: %r' % counts)
    rep['execution_counts'] = counts
    tables, plots, fits = NB['table_cells'], NB['plot_cells'], NB['fit_cells']
    stream_only = NB['stdout_cells']
    png_cells = {}
    for c in code:
        cid = c.get('id')
        outs = c.get('outputs', [])
        for o in outs:
            check(o.get('output_type') != 'error', 'error output in ' + cid)
            body = json.dumps(o)
            check('Traceback' not in body, 'traceback text in ' + cid)
            check(not re.search(r'(?i)warning', text_of(o)) or cid in NB['warning_word_ok_cells'],
                  'warning text in ' + cid)
            if o.get('output_type') == 'stream' and o.get('name') == 'stderr':
                check(cid in fits, 'stderr outside fit cells: ' + cid)
                for line in text_of(o).splitlines():
                    check(not line.strip() or any(p.fullmatch(line.strip()) for p in STDERR_ALLOWED),
                          'unexpected stderr line in %s: %s' % (cid, line[:200]))
        pngs = [o for o in outs if 'image/png' in o.get('data', {})]
        if pngs:
            png_cells[cid] = pngs
        if cid in tables:
            results = [o for o in outs if o.get('output_type') == 'execute_result']
            check(len(results) == 1, 'table cell without one result: ' + cid)
            html = text_of(results[0], 'text/html')
            plain = text_of(results[0])
            check('<table' in html and plain.strip(), 'table output missing: ' + cid)
            for needle in tables[cid]:
                check(needle in plain or needle in html, 'expected %r in table %s' % (needle, cid))
        elif cid in plots:
            check(len(pngs) == 1, 'plot cell must have exactly one PNG: ' + cid)
        elif cid in fits:
            check(not any(o.get('output_type') == 'execute_result' for o in outs), 'fit cell result: ' + cid)
            check(all(o.get('output_type') in ('stream', 'display_data') for o in outs), 'fit output kind: ' + cid)
        elif cid in stream_only:
            check(outs and all(o.get('output_type') == 'stream' and o.get('name') == 'stdout' for o in outs),
                  'stdout-only cell: ' + cid)
        else:
            check(not outs, 'unexpected output in silent cell: ' + cid)
    check(sorted(png_cells) == sorted(plots), 'PNG outputs outside the four plot cells: %r' % sorted(png_cells))
    rep['plots'] = {}
    for cid, outs in png_cells.items():
        raw = base64.b64decode(''.join(outs[0]['data']['image/png']).encode().translate(None, b' \t\r\n'),
                               validate=True)
        check(raw.startswith(PNG) and NB['png_min_bytes'] <= len(raw) <= 2 * 1024 ** 2, 'PNG invalid: ' + cid)
        rep['plots'][cid] = {'bytes': len(raw), 'sha256': sha(raw)}
    check(len(rep['plots']) == 4, 'four nonempty plots')
    serialized = json.dumps([c.get('outputs', []) for c in code])
    leaked = [v for v in forbidden_values() if v in serialized]
    check(not leaked, 'private runtime detail in outputs (%d values)' % len(leaked))
    check(not any(p.search(serialized) for p in SECRET_PATTERNS), 'credential-like material in outputs')
    check(all('a10_probe' not in c.source for c in nb.cells), 'probe in public notebook')
    rep['forbidden_scan'] = {'values_checked': len(forbidden_values()), 'patterns': len(SECRET_PATTERNS)}
    return rep


def main():
    reviewed_bytes = REVIEWED.read_bytes()
    check(sha(reviewed_bytes) == NB['reviewed_sha256'], 'reviewed notebook hash')
    check(TARGET.read_bytes() == reviewed_bytes, 'source notebook is not the reviewed unexecuted notebook')
    reviewed = nbformat.reads(reviewed_bytes.decode('utf-8'), as_version=4)
    check(all(not c.get('outputs') for c in reviewed.cells if c.cell_type == 'code'), 'reviewed has outputs')
    nb = nbformat.reads(reviewed_bytes.decode('utf-8'), as_version=4)
    probe = nbformat.v4.new_code_cell(probe_source())
    nb.cells.append(probe)
    started = time.monotonic()
    first = None
    report = {}
    try:
        NotebookClient(nb, timeout=NB['cell_timeout_seconds'], kernel_name='python3', allow_errors=False,
                       resources={'metadata': {'path': str(TARGET.parent)}}).execute()
        check(nb.cells[-1].source == probe.source, 'probe cell moved')
        check(not any(o.get('output_type') == 'error' for o in nb.cells[-1].outputs), 'probe failed')
        probe_report = json.loads((E / 'science-probe.json').read_text())
        check(probe_report.get('status') == 'PASS', 'probe did not pass')
        nb.cells.pop()
        fit_health = check_notebook_outputs(nb, NB['fit_cells'])
        report = verify(nb, reviewed)
        nbformat.validate(nb)
        nbformat.write(nb, str(TARGET))
        data = TARGET.read_bytes()
        check(len(data) <= NB['max_bytes'], 'executed notebook too large')
        check(sha(data) != NB['reviewed_sha256'], 'executed notebook identical to unexecuted source')
        (E / 'executed-causal_effects.ipynb').write_bytes(data)
        report.update({'executed_sha256': sha(data), 'executed_bytes': len(data),
                       'reviewed_sha256': NB['reviewed_sha256'], 'probe_published': False,
                       'probe_status': probe_report['status'], 'fit_health': fit_health})
    except BaseException as exc:
        first = {'type': type(exc).__name__, 'message': str(exc)[-4000:]}
        raise
    finally:
        nbformat.write(nb, str(E / 'notebook-execution-retained.ipynb'))
        receipt = {'firstFailure': first, 'seconds': time.monotonic() - started, 'automaticRetry': False,
                   'codeCellsExecuted': sum(c.get('execution_count') is not None
                                            for c in nb.cells if c.cell_type == 'code'),
                   'report': report}
        text = json.dumps(receipt, indent=1, sort_keys=True, default=str, allow_nan=False) + '\n'
        tmp = E / '.notebook-receipt.json.tmp'
        tmp.write_text(text)
        tmp.replace(E / 'notebook-receipt.json')


if __name__ == '__main__':
    main()
