# Offline reproduction

The ZIP contains exact candidate source, exact S1 input source, both patches and identities, frozen v3 design/review, independent tests/review, and small raw result logs. It does not contain an interpreter, installed dependencies, caches, generated Registry databases or native run artifacts. No installation or native execution is authorized by this document.

## Static identity and patch

Use an existing authorized Python interpreter. From this package directory:

```
/path/to/existing/python tools/verify_identity.py
cp -a inputs/S1-source /tmp/unique-disposable-h7-reconstruction
git -C /tmp/unique-disposable-h7-reconstruction apply --check /absolute/package/H7-core.patch
git -C /tmp/unique-disposable-h7-reconstruction apply /absolute/package/H7-core.patch
/path/to/existing/python tools/source_manifest.py /tmp/unique-disposable-h7-reconstruction > /tmp/h7-reconstruction.json
cmp /tmp/h7-reconstruction.json evidence/final21-source-after.json
```

Choose a previously nonexistent disposable destination. The included S1 source already contains the S1 patch; do not apply S1 twice. Its base/main provenance is a pinned historical input, not a statement about the current remote HEAD. The snapshot retains its declared missing examples/history and is not a complete repository clone.

## Selected test run

If the authorized environment already has the required dependencies (recorded in `evidence/python-environment.json`), run:

```
RPNH_PYTHON=/path/to/existing/python bash tools/run_final_offline.sh local-final
```

This selects 31 author core and 68 adjacent tests, with three explicit subprocess tests deselected. The import-time sentinel rejects network, native/subprocess and PTY operations. The wrapper records source manifests before/after, the real pytest exit, log and XML. Do not edit source or the runner while a run is active. Do not install missing dependencies, enable plugin autodiscovery or remove the sentinel merely to obtain a pass.

The independent original tests can be rerun against this same source using:

```
/path/to/existing/python independent-review/run_d0.py /absolute/package/source /absolute/package/evidence/local-independent-results.json -v --tb=short -p no:cacheprovider /absolute/package/independent-review/tests
```

The independent launcher supplies its own import-time sentinel and inserts the candidate source/test paths. It requires the same existing dependencies. Independent archived result files retain original absolute paths as historical evidence; those paths are not expected to exist on a new machine.

Scripts that generated or hardened the initial candidate are preserved for traceability, not as setup commands. Do not rerun them over the frozen source. The source/patch verification scripts do not import the product.

## Interpretation

A pass establishes only the selected real Registry/PN offline behavior. The native evidence in tests is explicitly injected; it does not authenticate an OS peer, reserve a physical target or prove either real wrapper. Use `VALIDATION.md`, `PROTECTED_CLOSURE_AUDIT.md`, `NATIVE_HANDOFF.md` and `LOCAL_FOLLOWUP_TASK.md` for remaining requirements. H7 full D0, D1 and H7b remain incomplete/unverified.
