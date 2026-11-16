# RPNH bootstrap transport

## Linux distribution direction (pending release)

Future Linux RPNH releases use an official npm package and its commands as the installation and update entry. That entry must invoke the original RPNH install/update flow, provide the corresponding release materials and advance the actual application version. Updating this transport package alone is not an RPNH application upgrade. The original Python core, lifecycle confirmation and user-data handling remain in charge.

The release work must establish and validate the public package name, version mapping, prerequisites and complete user commands. The private tarball and explicit-wheel examples below validate the current candidate. Public registry installation and actual cross-release RPNH updates remain uncompleted release work.

This source package launches the bootstrap module from an explicitly selected RPNH wheel. It does not contain a second copy of the Python installer or manage application versions. The wheel also provides `rpnh-bootstrap` as a Python console command; the installed application remains `rpnh`.

This package is currently private and unpublished. Use a locally built npm tarball; no registry package name or public release is promised. Node 20+, an installed `uv`, a persistent controller Python and a matching RPNH wheel containing `cpn.rpnh.bootstrap_entry` are required. Old wheels without that module cannot use this entry.

```sh
npm pack --ignore-scripts
npm install --ignore-scripts --prefix ./bootstrap-tool ./rpnh-bootstrap-0.0.0-private.tgz
./bootstrap-tool/node_modules/.bin/rpnh-bootstrap \
  --controller-wheel /absolute/path/rpnh_harness.whl \
  --controller-python /absolute/path/python3 \
  -- --root /absolute/path/rpnh-root --release vVERSION \
  --channel prerelease --platform /absolute/path/platform.json \
  --python /absolute/path/base-python
```

The wheel filename above is illustrative; supply the actual built wheel filename. The release must expose metadata/assets accepted by the original published installer; this package does not create or invent them. A local controller wheel alone does not replace release assets. For air-gapped validation, `--offline` before `--` applies to uv; it does not make the original release resolver offline. A prepared local metadata transport is needed for that separate testing boundary.

For an explicit local candidate, use this alternative after preparing the private npm tarball and controller wheel:

```sh
./bootstrap-tool/node_modules/.bin/rpnh-bootstrap \
  --controller-wheel /absolute/path/controller.whl \
  --controller-python /absolute/path/python3 --offline \
  -- --root /absolute/path/rpnh-root \
  --candidate-wheel /absolute/path/candidate.whl \
  --materials /absolute/path/materials.json --inspect-only --json
```

Supply actual wheel filenames. The controller wheel must contain this local candidate entry; the candidate wheel is the explicit application payload. Remove `--inspect-only` to request installation through the original `update apply`, including its terminal `UPDATE` confirmation. Inspection delegates to the original `update plan`; neither path invents a published release. Materials own the exact platform, runtime bindings and extras, and remain subject to the original payload, identity and consistency checks. This entry does not generate materials.

`--release` and `--candidate-wheel` are mutually exclusive. Local candidates require `--materials` and reject `--python`, `--runtime-bindings`, `--platform`, `--channel`, `--extra` and `--combination`. Published requests reject `--materials`. Local `--components FILE` and `--json` pass through to plan/apply; `--select` is available only for local apply and is rejected with local `--inspect-only`. `--bin-dir` is rejected with inspection and registration runs only after successful application.

All parameters after `--` go to the wheel's bootstrap module. For published requests, complete frontend selections use `--runtime-bindings FILE --components FILE`, optional `--select`, and repeated `--extra mcp/skills`; Basic may use `--python`. Platform facts remain explicit; no automatic native claim is generated. The controller Python is fixed and automatic Python downloads are disabled. uv owns only its temporary controller environment; original RPNH prepare/probe/current owns the application installation and confirmation.

Optional `--bin-dir DIRECTORY` links the original managed `rpnh` dispatcher in an existing real directory after installation. It never edits PATH or replaces another command. If registration fails, exit 3 means installation is retained; retry registration or use the original root/bin/rpnh. Current symlink registration evidence is Linux-only, not native Windows support. Private package status is a release guard, not a new product permissions model.

Python runs with `-I -m`: the controller ignores caller cwd and PYTHONPATH when locating the wheel module.
