# H2a: reproduce TaskControl reader convergence

Base: `Deng-0119/RPNH` commit `ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4`.
The cloud execution used Python 3.12 in the existing offline test environment.
Use an existing project test environment on the local machine. No provider,
model execution, GitHub Actions, install, remote write, or push is needed.

## Apply the reviewed patch

From a clean authoritative checkout with the four existing target files still
matching `file-manifest.json`:

```sh
git apply --check /path/to/taskcontrol-reader-convergence.patch
git apply /path/to/taskcontrol-reader-convergence.patch
```

Do not overwrite concurrent local edits. Inspect any conflict against the
manifest and review the resulting diff before testing. This patch is independent
of the H1 owner-entry convergence changes and does not include them.

## Focused consumer verification

From the repository root:

```sh
PYTHONPATH=. python -m pytest -q tests/test_task_control_registry_reads.py --junitxml=taskcontrol-reader.xml
```

These tests use temporary real Registries, explicit static products, corrupted
fixture descriptors for negative cases, and race probes. They never dispatch
an executor or a real model. The nonzero count fixture installs a synthetic
historical count using Registry's existing imported-accounting API, then checks
that it remains cumulative through a reopened generation. It is not evidence
of actual external model calls.

## Existing affected-consumer regression checks

```sh
PYTHONPATH=. python -m pytest -q tests/test_run_descriptor_reads.py tests/test_package_result_projection.py tests/test_opencode_application_boundary.py tests/test_basic_cli_task_switching.py --junitxml=taskcontrol-compatibility.xml
PYTHONPATH=.:examples/harnessaudit_office/tests python -m pytest -q examples/harnessaudit_office/tests/test_registry_reader_consumers.py --junitxml=shared-reader.xml
```

## Native local completion gate

Run these on the supported local Linux/WSL2 environment with normal AF_UNIX
support. The cloud sandbox denies socket construction; this layer was not
passed and was not replaced with a pipe or a mocked transport.

```sh
PYTHONPATH=. python -m pytest -q tests/test_task_frontend.py -k 'task_control or task_status' --junitxml=taskcontrol-native.xml
PYTHONPATH=. python -m pytest -q tests/test_task_frontend.py::test_resume_uses_persisted_transition_profiles_when_graph_swaps_them --junitxml=taskcontrol-native-resume.xml
```

The fixtures use scripted offline profiles. Do not substitute a provider-backed
profile or run a live task to validate this read-only change.

## Old-red evidence

The unchanged baseline was tested with the new targeted regressions by selecting
its source through PYTHONPATH, rather than by reverting the implementation:

```sh
cd baseline
PYTHONPATH=. python -m pytest -q ../source/tests/test_task_control_registry_reads.py --import-mode=importlib -k 'counts_race or payload_decode_race or wrong_canonical or descriptors_default or descriptor_backing or product_backing or reader_rejection' --junitxml=../old-red.xml
```

Expected result recorded in `old-red.log`: 20 failures. They demonstrate the
missing final guards, native closure delegation and bounded backing reads in
the old consumers. They are not regressions in the patched source.
