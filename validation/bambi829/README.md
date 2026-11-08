# Bambi #829 native baseline measurement

Private, unexecuted packet. No getter implementation and no contribution PR.

Exact current dev source: 9552e1d0ece1ce3c209f9b17fa656c2e57924970;
tree de552c46d97d8cd24c027fa73936b6b7a321400f. The readonly local cache
and seven public datasets were verified separately. Runtime uses a genuine
full Git checkout for native SCM metadata, never a synthetic package version.

One prospective hosted AMD64 Docker measurement, attempt 1 of 2 for this issue.
Execution is disabled. No retry, rerun, corrected ref or clock reset is granted.
The candidate, if subsequently admitted, has only the remaining attempt.
The enabling push must be the FIRST push containing this workflow file.
Never publish the disabled draft first: that would consume workflow run #1.
Admission failure before project execution does not consume validation attempt
1, but the workflow identity is still consumed. Any later route needs fresh
review and a new workflow identity; rerunning or resetting this one is forbidden.

The first workflow step anchors time and received bytes before checkout.
The network cap is observed on entry to and throughout the owned controller;
it cannot interrupt an already-running checkout action. Checkouts have their
own three-minute limits. All project execution stops at 3000 seconds from the
one origin, with teardown and upload bounded within the 3600-second absolute.

Actual prerequisite admission: native AMD64, four CPUs, cgroup v2, 14 GiB
available memory and 20 GiB free Docker disk. The disk estimate covers a 6–8
GiB native environment, package cache, base image, Quarto, scientific compiler
cache, results and headroom. Each scientific phase has a 10 GiB memory cap,
4096 PIDs, four CPU affinity and 4 GiB SHM. These are workload-specific, not
claims that launch admission has passed.

Use the complete configured py312 environment: dev, test, docs, JAX and Nutpie.
This is one supported CVM lane. The five other upstream PR CI lanes are
corroboration, not claimed local passes. PR1087's actual py312 CVM pytest step
took 1425 seconds with coverage and a confirmed compilation-cache miss, under identical pyproject and workflow object
IDs; source/tests are not identical. A 1800-second pytest cap replaces the
earlier unsupported 18-minute proposal. Before pytest, require 1920 seconds
remaining; otherwise stop without starting an inevitably partial test run.

Sequence: image admission; real success/exit/timeout/OOM/deadline fixtures;
changed HTTP/tar/logging/native-work-mount fixtures; official digest-verified
Pixi and Quarto; full native solve/install; compiler, optional imports and model
build; hook installation; quartodoc build/interlinks, existing-page render and
real two-cell notebook; actual pytest observer pass/fail/skip fixture; offline
imports, all-file pre-commit, pylint, collection and entire pytest tests suite;
explicit bounded caches and lock/list evidence. No sampling or assertions are
replaced. Baseline lint autofixes are retained, restored only in the owned
baseline copy, and the complete 275-file source identity is rechecked.

Each test's actual node ID and setup/call/teardown reports are retained. A
completed measurement must match both collections and have teardown results
for every node, plus JUnit details. Failures/skips still require substantive
classification: missing dependency, path or resource failures do not establish
closure. A baseline product/lint failure is not a candidate pass.

No Docker socket, provider credentials, home directory or environment dumps
enter containers or artifacts. Contents permission is read-only. The explicit
evidence directory is the only uploaded output. Caches are capped at 40 MiB
of regular-file input, excluding all .git material/configs/symlinks; this is
partial reuse, not a restorable whole environment or complete hook cache.
The solve lock and exact environment list are mandatory retained outputs.
No whole environment or image archive is needed for this measurement.

Containers are individually labelled, stopped and inspected even on failure;
there is no rm/prune/reset. The entire venue is hosted and ephemeral, with no
access to this Mac or its external disk.
