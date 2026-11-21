"""Fit-output health gate shared by notebook execution and offline tests."""
import re


def output_text(output):
    if output.get("output_type") == "stream":
        value = output.get("text", "")
    else:
        value = output.get("data", {}).get("text/plain", "")
    return "".join(value) if isinstance(value, list) else value


def check_notebook_outputs(notebook, fit_ids):
    text = "\n".join(
        output_text(output)
        for cell in notebook["cells"] if cell.get("id") in set(fit_ids)
        for output in cell.get("outputs", [])
    )
    failures = [phrase for phrase in ("Increase `target_accept`", "at least 4 chains")
                if phrase in text]
    divergence = re.search(r"\b(?:There (?:was|were) )?(\d+) divergences? after tuning\b", text)
    if divergence and int(divergence.group(1)) != 0:
        failures.append(divergence.group(0))
    if failures:
        raise AssertionError("fit health output: " + "; ".join(failures))
    return {"status": "PASS", "fit_cells": sorted(fit_ids), "forbidden_matches": []}
