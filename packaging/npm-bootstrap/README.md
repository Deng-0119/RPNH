# RPNH bootstrap transport

## Published RC9 and this source template

[RPNH v0.1.0rc9](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc9) is the current GitHub prerelease. Its published npm package is [`rpnh-bootstrap@0.1.0-rc.9`](https://www.npmjs.com/package/rpnh-bootstrap/v/0.1.0-rc.9); `latest` and `next` both pointed to RC9 after release. Anonymous fresh installation with `npm exec --package=rpnh-bootstrap -- rpnh` passed on WSL2 Linux x86_64 with persistent CPython 3.12.13 (Basic), including activation, first setup, and offline doctor. This does not qualify upgrades, rollback, other platforms, or optional frontends. See the [installation guide](../../docs/guides/linux-npm-candidate.md) for prerequisites. Updating the npm wrapper alone does not upgrade the RPNH application; the original installer and lifecycle confirmation apply.

The checked-in npm source template is private. The commands below exercise a locally built template tarball, not the registry package. They require Node.js 20+, `uv`, persistent controller Python, and a matching RPNH wheel containing `cpn.rpnh.bootstrap_entry`. Older wheels without that module cannot use this entry. The template invokes the chosen wheel; it does not ship a second installer or manage application versions. The wheel also provides `rpnh-bootstrap` as a Python console command; the installed application command is `rpnh`.

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

## Assembling release packages

Use `prepare_first_run_candidate.py` with the matching core wheel, launcher sources, license, npm version and a new output directory. An application version such as `0.1.0rc9` corresponds to npm version `0.1.0-rc.9`.

`--public-cli` assembles an `rpnh` tarball with the `rpnh` command and npm publication metadata targeting `latest`; assembly does not publish it. The attempted RC9 publication under the short package name `rpnh` was rejected. The published package is `rpnh-bootstrap`. The `--public-preview` option assembles that package with the `rpnh` and `rpnh-bootstrap` commands and publication metadata targeting `next`. The default output is a private candidate. Both assembly modes use the original installer and matching GitHub Release assets.

Run the offline assembly checks with `python packaging/npm-bootstrap/test-prepare-first-run.py`.
