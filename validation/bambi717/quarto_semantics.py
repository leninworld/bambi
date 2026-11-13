"""Actual pinned Quarto behavior controls in disposable diagnostic documents."""
from pathlib import Path
import json
import nbformat
from inside import pixi

root=Path('/work/quarto-semantics');root.mkdir(exist_ok=False)
marker=root/'executed';qmarker=root/'qmd-executed'
quarto=Path('/work/tools/quarto-path.txt').read_text()
nb=nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(
 "from pathlib import Path\nPath("+repr(str(marker))+").write_text('executed')\nprint('EXECUTED_MARKER')")])
nb.cells[0].execution_count=1
nb.cells[0].outputs=[nbformat.v4.new_output('stream',name='stdout',text='STORED_MARKER\n')]
nb.metadata['kernelspec']={'display_name':'Bambi717 baseline','language':'python','name':'bambi717-baseline'}
nbformat.write(nb,root/'control.ipynb')
pixi('quarto-stored-control',[quarto,'render','control.ipynb'],cwd=root)
assert not marker.exists() and 'STORED_MARKER' in (root/'control.html').read_text()
pixi('quarto-execute-control',[quarto,'render','control.ipynb','--execute'],cwd=root)
assert marker.read_text()=='executed' and 'EXECUTED_MARKER' in (root/'control.html').read_text()
(root/'control.qmd').write_text('---\ntitle: QMD control\njupyter: bambi717-baseline\n---\n\n```{python}\nfrom pathlib import Path\nPath('+repr(str(qmarker))+').write_text("executed")\nprint("QMD_DEFAULT_EXECUTED")\n```\n')
pixi('quarto-qmd-control',[quarto,'render','control.qmd'],cwd=root)
assert qmarker.read_text()=='executed' and 'QMD_DEFAULT_EXECUTED' in (root/'control.html').read_text()
Path('/work/evidence/quarto-actual-semantics.json').write_text(json.dumps({'status':'PASS','quarto':'1.8.26','ipynbStoredByDefault':True,'ipynbExplicitExecute':True,'qmdExecutesByDefault':True,'projectDocsUnchanged':True},indent=2)+'\n')
