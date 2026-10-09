# Reproduce without installation or native execution

Use an existing authorized Python containing the input dependencies. This package includes no interpreter or dependencies, and does not authorize installing them.

From the package directory:

```
RPNH_PYTHON=/path/to/existing/python bash tools/run_final_offline.sh reproduced
```

The sentinel is installed before pytest/product imports and blocks socket, URL, subprocess, process-spawn and PTY actions. Plugin autodiscovery is disabled. The one explicitly subprocess-based cold-process test is deselected; in-process readonly cold-Core hydration is separately exercised. No sentinel may be disabled to obtain a pass.

For each run the launcher records exact source manifests before and after, collected pytest node IDs, actual phase outcomes, sentinel events, real exit code, log and JUnit XML. Final evidence must use the same frozen source identity; intermediate reruns do not add distinct test cases.

## Patch reconstruction

`inputs/acceptance-history-source/` is the exact 994-file input; it already includes the H7 core and acceptance-history overlay. Do not reapply either earlier patch.

```
cp -a inputs/acceptance-history-source /tmp/unique-bound-child-reconstruction
git -C /tmp/unique-bound-child-reconstruction apply --check /absolute/package/bound-child-declarations.patch
git -C /tmp/unique-bound-child-reconstruction apply /absolute/package/bound-child-declarations.patch
/path/to/existing/python tools/source_manifest.py /tmp/unique-bound-child-reconstruction > /tmp/reconstructed.json
cmp /tmp/reconstructed.json evidence/FINAL_SOURCE_MANIFEST.json
```

Choose an absent disposable path. The included evidence records a successful local check/apply and exact manifest comparison. Source snapshots are curated frozen input packages, not complete Git clones or claims about live main.

Read IMPLEMENTATION_SCOPE.md before interpreting results. In particular, declaration digests do not cover private profile contents, installed code closure, credential bindings or arbitrary HOST services, and the declaration DTO is rejected at the H7 intent boundary. Test-only evidence is not a production native issuer. Native worker/reservation/receipt and parent terminal completion remain unimplemented/NOT_RUN.

## Included independent review

The `review/` report, tests, original runners, inputs and evidence are exact copies of the frozen independent review. Its original shell runners describe the reviewer's `reviewed-source/` layout; those originals have not been edited. This self-contained package reuses the identical root `source/` rather than duplicating that 999-file snapshot.

Use the package-level portable wrapper, which sets the working directory to root `source/` and invokes the original independent Python sentinel/runner with the included test paths:

```
RPNH_PYTHON=/path/to/existing/python bash tools/run_independent_offline.sh
```

This reproduces the 46 new independent probes plus the ordinary structural-revision control (47 cases). Combine their unique IDs with the 721 cases in `run_final_offline.sh` for the final 768-case union. The original reviewer independently reran all 721 common cases too; those repeated executions count once. A packaged collection-only probe verified all 47 wrapper-selected cases import correctly with no external boundary attempts; it is not counted as another test pass.

`JOINT_TEST_COUNTS.json` is computed from both final evidence inventories, not by adding reported totals. The six additional native-net-operation cases missing a frozen example input remain NOT_RUN; no external file was added to make collection pass.
