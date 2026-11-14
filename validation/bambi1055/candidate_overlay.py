"""Exact nine contribution inputs; executed notebook outputs are the only runtime change."""
import ast,copy,hashlib,json,os,shutil
from pathlib import Path
P=Path('/packet');SRC=Path('/work/src');E=Path('/work/evidence')
PATHS=('bambi/causal/__init__.py', 'bambi/causal/effects.py', 'bambi/causal/types.py', 'bambi/causal/validate.py', 'bambi/causal/plots.py', 'tests/test_causal.py', 'docs/notebooks/causal_effects.ipynb', 'pyproject.toml', 'docs/_quarto.yml')
OVERLAYS=set(PATHS)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def validate_inputs(rows,root):
 assert set(rows)==set(PATHS)
 for n,row in rows.items():
  f=root/n;assert f.is_file() and not f.is_symlink()
  assert f.stat().st_size==row['bytes'] and sha(f)==row['sha256']
 return rows
def inputs():
 return validate_inputs(json.loads((P/'candidate-files.json').read_text()),P/'candidate')
def source_contract_controls():
 rows=inputs();rejected=[]
 for name in ('missing','wrong-hash','wrong-size'):
  changed=copy.deepcopy(rows)
  if name=='missing':changed.pop(PATHS[0])
  elif name=='wrong-hash':changed[PATHS[0]]['sha256']='0'*64
  else:changed[PATHS[0]]['bytes']+=1
  try:validate_inputs(changed,P/'candidate')
  except AssertionError:rejected.append(name)
  else:raise AssertionError('Invalid actual source input accepted: '+name)
 return {'status':'PASS','actualInputsUsed':True,'rejected':rejected,'sourceNotMutated':True}
def notebook_sources(path):
 n=json.loads(Path(path).read_text());assert n['nbformat']==4
 return [(c['cell_type'],''.join(c['source'])if isinstance(c['source'],list)else c['source'])for c in n['cells']]
def apply_candidate():
 rows=inputs()
 for n in PATHS:
  f=SRC/n;f.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(P/'candidate'/n,f)
 (Path('/work')/'candidate-active.json').write_text(json.dumps({'paths':PATHS,'inputHashes':{n:r['sha256']for n,r in rows.items()}})+'\n')
 verify_candidate()
def verify_candidate():
 rows=inputs()
 for n in PATHS:
  f=SRC/n;assert f.is_file() and not f.is_symlink()
  if n.endswith('.ipynb') and (E/'candidate-notebook-identity.json').exists():
   proof=json.loads((E/'candidate-notebook-identity.json').read_text());assert proof['exactReviewedCellSourcesVerified']
   assert notebook_sources(f)==notebook_sources(P/'candidate'/n)
   assert sha(f)==proof['executedNotebookSHA256']
  elif n in PATHS[:6] and (E/'candidate-format-proof.json').exists():
   proof=json.loads((E/'candidate-format-proof.json').read_text())
   assert proof['status']=='PASS' and proof['blackVersion']=='25.9.0' and proof['allPythonASTIdentical']
   assert proof['pythonPaths']==list(PATHS[:6]) and proof['exactContributionPathUnionUnchanged'] and proof['notebookBytesUnchanged']
   original=P/'candidate'/n
   assert proof['beforeHashes'][n]==rows[n]['sha256'] and sha(f)==proof['afterHashes'][n]
   normalized=ast.dump(ast.parse(original.read_text()),include_attributes=False)
   assert normalized==ast.dump(ast.parse(f.read_text()),include_attributes=False),'POSTFORMAT_SEMANTIC_CHANGE'
   assert hashlib.sha256(normalized.encode()).hexdigest()==proof['normalizedASTSHA256'][n]
  else:assert sha(f)==rows[n]['sha256'],n
 return {n:sha(SRC/n)for n in PATHS}
