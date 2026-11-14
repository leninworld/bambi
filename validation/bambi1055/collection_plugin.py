"""Record real pytest identity, phase duration and wall-clock progress without filtering tests."""
import datetime
import json
import os
from pathlib import Path
import time


def timestamp():
    return {'atUTC': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'wallTime': time.time(), 'monotonic': time.monotonic()}


def progress(event, nodeid):
    path = os.environ.get('M0_TIMING_OUTPUT')
    if path:
        with Path(path).open('a') as stream:
            stream.write(json.dumps({'event': event, 'nodeid': nodeid, **timestamp()}) + '\n')


def pytest_collection_finish(session):
    Path(os.environ['M0_COLLECTION_OUTPUT']).write_text(
        json.dumps([item.nodeid for item in session.items])+'\n')


def pytest_runtest_logstart(nodeid, location):
    progress('start', nodeid)


def pytest_runtest_logfinish(nodeid, location):
    progress('finish', nodeid)


def pytest_runtest_logreport(report):
    path = os.environ.get('M0_REPORT_OUTPUT')
    if path:
        with Path(path).open('a') as stream:
            stream.write(json.dumps({'nodeid': report.nodeid, 'when': report.when,
                                     'outcome': report.outcome, 'duration': report.duration,
                                     **timestamp()})+'\n')
