# Source and data boundaries

- RPNH integration patterns are adapted from the same project's `examples/tool_pipeline`
  at `b8d5942e768b2d59862b8e3eb7efa41a274f6307`, under RPNH's MIT license.
- Terminal-Bench 2 task source is published by `harbor-framework/terminal-bench-2`
  under Apache-2.0 at the pinned source commit in `sources.json`. This candidate
  contains links, interface expectations and hashes, not copied task, golden,
  verifier or solution files. The benchmark canary remains untouched upstream.
- The task's OEWN SQLite file is hosted in a separately pinned Hugging Face dataset.
  Its applicable artifact license/provenance was not established from available
  metadata. Neither the database nor its rows are redistributed in this candidate.
  The code accepts task-owner-supplied authorized material only.
- The [official OEWN project](https://github.com/globalwordnet/english-wordnet/blob/main/LICENSE.md)
  currently describes its data as CC-BY-4.0 with attribution to Princeton WordNet
  and the OEWN team. This does not establish the release, conversion provenance
  or complete contents of the pinned third-party SQLite artifact. Authorized
  task-local retrieval/use and public redistribution require separate consideration;
  the unresolved artifact information is not a finding that use is prohibited.
- The three-row selfcheck database is original synthetic wiring data. It is
  generated at runtime and cannot be used to claim Terminal-Bench task success.
