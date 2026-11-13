"""Record actual pytest identities/results; no test filtering or assertion changes."""
import json
import os
from pathlib import Path


def pytest_collection_finish(session):
    Path(os.environ['M0_COLLECTION_OUTPUT']).write_text(
        json.dumps([item.nodeid for item in session.items])+'\n')


def pytest_runtest_logreport(report):
    path = os.environ.get('M0_REPORT_OUTPUT')
    if path:
        with Path(path).open('a') as stream:
            stream.write(json.dumps({'nodeid': report.nodeid, 'when': report.when,
                                     'outcome': report.outcome})+'\n')
