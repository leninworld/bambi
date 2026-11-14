"""Exact nine contribution inputs; executed notebook outputs are the only runtime change."""
import ast,copy,hashlib,json,os,shutil,sys
from pathlib import Path
P=Path('/packet');SRC=Path('/work/src');E=Path('/work/evidence')
PATHS=('bambi/causal/__init__.py', 'bambi/causal/effects.py', 'bambi/causal/types.py', 'bambi/causal/validate.py', 'bambi/causal/plots.py', 'tests/test_causal.py', 'docs/notebooks/causal_effects.ipynb', 'pyproject.toml', 'docs/_quarto.yml')
OVERLAYS=set(PATHS)
def normalized_ast(raw):
 text=raw.decode('utf-8')if isinstance(raw,bytes)else raw
 options={'include_attributes':False}
 if sys.version_info>=(3,13):options['show_empty']=True
 return ast.dump(ast.parse(text),**options)
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
   normalized=normalized_ast(original.read_bytes())
   assert normalized==normalized_ast(f.read_bytes()),'POSTFORMAT_SEMANTIC_CHANGE'
   assert hashlib.sha256(normalized.encode()).hexdigest()==proof['normalizedASTSHA256'][n]
  else:assert sha(f)==rows[n]['sha256'],n
 return {n:sha(SRC/n)for n in PATHS}

def cross_interpreter_controls():
 # Called by outer pinned3.13 image after the actual selected-Pixi producer completed.
 assert sys.version_info[:2]==(3,13),'PINNED_OUTER_PYTHON_313_REQUIRED'
 proof=json.loads((E/'candidate-format-proof.json').read_text())
 producer=proof['producerPythonVersion']
 expected_minor=12 if os.environ['G0_ENV']=='py312'else 13
 assert producer[:2]==[3,expected_minor]
 verified=verify_candidate();checked=[];rejected=[]
 for n in PATHS[:6]:
  original=(P/'candidate'/n).read_bytes();formatted=(SRC/n).read_bytes()
  canonical=normalized_ast(original)
  assert canonical==normalized_ast(formatted)
  assert hashlib.sha256(canonical.encode()).hexdigest()==proof['normalizedASTSHA256'][n]
  mutation=formatted+b'\n_ast_control_semantic_mutation = 1\n'
  try:assert canonical==normalized_ast(mutation),'ACTUAL_SEMANTIC_MUTATION_REJECTED'
  except AssertionError:rejected.append(n)
  else:raise AssertionError('Actual source semantic mutation accepted: '+n)
  checked.append(n)
 assert checked==list(PATHS[:6])and rejected==checked
 result={'status':'PASS','producerPythonVersion':producer,'consumerPythonVersion':list(sys.version_info[:3]),'actualCrossMinorBoundary':producer[1]!=sys.version_info.minor,'allSixOriginalFormattedAndProducerHashesMatch':True,'paths':checked,'semanticMutationRejectedForEach':rejected,'sourceHashesUnchanged':verified,'normalization':'include_attributes=False; show_empty=True on3.13+, originalsignature3.12','sourceNotMutated':True}
 (E/'cross-interpreter-AST-controls.json').write_text(json.dumps(result,indent=2)+'\n')
 return result
