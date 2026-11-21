"""Exercise every straight-line authored notebook call using small real native fits."""
import ast
import copy
import json
from pathlib import Path

import nbformat
from nbclient import NotebookClient

E = Path('/work/evidence')
P = Path('/packet')


def run_public_api_gate():
    source = nbformat.read(P / 'candidate-mediation_analysis.ipynb', as_version=4)
    gate = copy.deepcopy(source)
    calls = []
    changed = []
    for index, cell in enumerate(gate.cells):
        if cell.cell_type != 'code':
            continue
        text = '\n'.join(line for line in cell.source.splitlines() if not line.startswith('%'))
        tree = ast.parse(text)
        # A completed cell exercises every call only because its source is unconditional.
        assert not any(isinstance(node, (ast.If, ast.For, ast.While, ast.Try, ast.IfExp,
                                         ast.FunctionDef, ast.Lambda, ast.ListComp,
                                         ast.DictComp, ast.SetComp, ast.GeneratorExp))
                       for node in ast.walk(tree))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            calls.append({'cell': index, 'id': cell.id, 'call': ast.unparse(node.func),
                          'arguments': ast.unparse(node)})
            if isinstance(node.func, ast.Attribute) and node.func.attr == 'fit':
                actual = {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords}
                assert (actual['draws'], actual['tune'], actual['chains'], actual['cores']) == (1000, 4000, 4, 4)
                assert actual['nuts'] == {'target_accept': 0.9}
                assert actual['random_seed'] in (421, 431, 433) and actual['progressbar'] is False
                for keyword in node.keywords:
                    if keyword.arg in ('draws', 'tune', 'chains', 'cores'):
                        keyword.value = ast.Constant(20 if keyword.arg in ('draws', 'tune') else 2)
                changed.append({'cell': index, 'declaredFullSettings': actual,
                                'gateOnlyCounts': {'draws': 20, 'tune': 20, 'chains': 2, 'cores': 2}})
        # Preserve magics and exact sources in cells that do not contain sampling.
        if any(row['cell'] == index for row in changed):
            cell.source = ast.unparse(ast.fix_missing_locations(tree))
    assert len(changed) == 3 and len({row['declaredFullSettings']['random_seed'] for row in changed}) == 3
    first = None
    try:
        NotebookClient(gate, timeout=420, kernel_name='bambi717-baseline',
                       resources={'metadata': {'path': '/work/src/docs/notebooks'}}).execute()
        assert all(c.execution_count is not None for c in gate.cells if c.cell_type == 'code')
        assert not any(o.output_type == 'error' for c in gate.cells if c.cell_type == 'code' for o in c.outputs)
        from visual_outputs import candidate_visual_contract
        visuals = candidate_visual_contract(gate)
        proof = {'status': 'PASS', 'actualPinnedDependenciesAndNativeFits': True,
                 'allAuthoredCallSitesCovered': True, 'straightLineCellsOnly': True,
                 'calls': calls, 'samplingOverridesOnly': changed,
                 'publicNotebookUnmodified': True, 'fullScientificPass': False,
                 'visuals': visuals}
        (E / 'public-api-gate.json').write_text(json.dumps(proof, indent=2) + '\n')
        return proof
    except BaseException as exc:
        first = {'type': type(exc).__name__, 'message': str(exc)[:4096]}
        raise
    finally:
        nbformat.write(gate, E / 'public-api-gate-executed-private.ipynb')
        (E / 'public-api-gate-terminal.json').write_text(json.dumps({
            'firstFailure': first, 'actualCompletedCells': sum(c.get('execution_count') is not None
                for c in gate.cells if c.cell_type == 'code'), 'declaredCalls': calls,
            'privateGateOnlyOverrides': changed, 'noRetry': True}, indent=2) + '\n')
