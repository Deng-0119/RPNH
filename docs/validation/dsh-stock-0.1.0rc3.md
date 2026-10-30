---
metadata:
  document-kind: validation-summary
  language: en
  counterpart: dsh-stock-0.1.0rc3_ZH.md
  status: scoped-prerelease-validation
---

# Stock DSH integration v0.1.0rc3: limited validation

Date: 2026-10-30. This is a sanitized summary, not a publication of user data,
private execution logs or a claim of all-product certification.

## Exact pair and environment

- RPNH Python `0.1.0rc3`, wheel `rpnh_harness-0.1.0rc3-py3-none-any.whl`.
- Operator `0.1.0-rc.3`, tarball `dsh-rpnh-operator-0.1.0-rc.3.tgz`.
- Linux, Python 3.12.14, Node 24.22.1; actual DSH app-boot/tools/native executor/
  system-prompt packages `0.2.2-alpha.2`, Cordis `4.0.6-alpha.1`, Schemastery
  `3.18.6-alpha.1`. Installed Python dependencies satisfied the unchanged bounds,
  including websockets 15.0.1, jsonschema 4.26.0 and packaging 26.3.

The source of the pair is the accompanying release tag. `cpn.__version__`,
Python distribution metadata, operator metadata and client version label were
checked against their stated versions. Older candidate results are not relabelled
as tests of this pair.

## Checks actually performed on the final bytes

1. **Artifact content:** the tarball has exactly nine product files, each matching
   its source. The wheel has 936 unique members: 928 source-mapped product/resource
   files and eight metadata/license files. Required MIT, upstream DSH and Viewer
   third-party license files remain present. No private development tree is used
   as the public source base.
2. **Real non-editable installation:** the final wheel was installed in a new
   isolated virtual environment. `pip check` passed; source-external `-I` imports
   resolved to that environment's site-packages and reported `0.1.0rc3`.
3. **Nine direct checks:** installed imports; empty-root list; extra field,
   unknown operation, blank task id, missing reopen reason, control-disabled stop,
   and missing root rejection; updates-off status. Error cases returned the
   expected failure rather than dispatching a control operation.
4. **Two separately audited wrapper checks:** list and updates-off used an audit
   hook rejecting network connect/bind and product subprocess calls; neither
   triggered it. This dynamic observation does not apply to the direct `-m` runs.
5. **Actual alpha.2 Loader pairing:** the installed tarball was not a source
   symlink. Official `boot` / `loadOverlayPatches` loaded its real bundle patch
   with native tool execution. Five RPNH read tools and zero RPNH control tools
   were registered. One native list call spawned the exact installed Python with
   `-I`, returned an empty list, and an extra interpreter-selection argument was
   rejected without another spawn. The context was disposed and the new root
   remained empty. No client UI or user task store was used.
6. **Pure guidance regression:** a synthetic offer verified that stock instructions
   omit unrelated managed-install commands. It does not prove release eligibility.

Dependency installation used official registries; Node install scripts were
suppressed. No additional native addon build was required for this limited check.
The final version-specific checks passed; this does not erase failures of earlier
preparation attempts or imply that every historical attempt succeeded.

## What this does not establish

- The profile-management CLI installation command is documented from upstream;
  the final pairing test used the real Loader directly, not that complete CLI flow.
- No final-pair browser/UI matrix, real approval/control/worker recovery, live model
  quality, latency/token saving or complete-session result is claimed here.
- No managed cold-install/upgrade/rollback qualification. There is no new supported
  automatic-update declaration. The stock plugin is manually installed.
- This is not system-wide network auditing. The task used isolated empty roots,
  no model configuration and no user service; dynamic guarantees are limited to
  the observations described above.

See [installation and operational limits](../../integrations/dsh/operator/README.md)
for the required matching pair and manual procedure.
