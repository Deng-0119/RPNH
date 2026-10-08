# Source provenance and licenses

This example's own adapter-preparation code is covered by the RPNH repository's
MIT license. It does not bundle upstream benchmark prompts, tests, reference
solutions, task data, runner code or dependencies.

- SlopCodeBench runner: SprocketLab, MIT, revision
  `31ceea3add480edb33431e70475c4c70597e6b31`.
  [License](https://github.com/SprocketLab/slop-code-bench/blob/31ceea3add480edb33431e70475c4c70597e6b31/LICENSE)
- Problem corpus: gabeorlanski/scb-problems, Apache-2.0, revision
  `9cd9ca3a51c3d3e2a99d2488a25baf73a2204451`.
  [License](https://github.com/gabeorlanski/scb-problems/blob/9cd9ca3a51c3d3e2a99d2488a25baf73a2204451/LICENSE)
- RPNH baseline: Deng-0119/RPNH, MIT, revision
  `ae09445fe1d9b973502bc5d2c961976c1d2c0163`.

Fetch upstream originals only when preparing an authorized local integration.
Keep their licenses, notices and benchmark canaries unchanged. Do not infer the
problem corpus license from a stale README badge or from the runner's license.
Upstream and third-party dependencies retain their own terms.
