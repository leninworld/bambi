"""Record unchanged hook actual version separately; never rename fallback as25.9.0."""
import json
import os
from pathlib import Path
import sqlite3
from inside import call, E

cache=Path(os.environ['PRE_COMMIT_HOME']).resolve()
with sqlite3.connect((cache/'db.db').as_uri()+'?mode=ro',uri=True)as db:
    rows=db.execute('SELECT path FROM repos WHERE repo=? AND ref=?',('https://github.com/psf/black','25.9.0')).fetchall()
assert len(rows)==1
repo=Path(rows[0][0]).resolve();assert repo.is_relative_to(cache)
bins=[p for p in repo.glob('py_env-*/bin/black')if p.is_file()];assert len(bins)==1
call('unchanged-upstream-black-hook-version',[str(bins[0]),'--version'])
(E/'actual-upstream-hook-version.json').write_text(json.dumps({
    'configuredRepository':'https://github.com/psf/black','configuredRef':'25.9.0',
    'actualCLIOutput':(E/'unchanged-upstream-black-hook-version.log').read_text(),
    'actualOutputNotRelabeledAsReleaseVersion':True,
    'hookConfigurationUnchanged':True,
    'fallbackSCMProvenanceUnprovenUnlessSeparateActualEvidence':True,
},indent=2)+'\n')
