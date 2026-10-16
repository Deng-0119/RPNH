# Public contribution policy

This repository contains the reusable RPNH product, its installation and usage
documentation, runnable examples, licenses and reviewed public result summaries.
Use [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance.

Preserve the installed `rpnh` entry point, independent task/workflow Registries,
user-owned provider and exact-model configuration, checkpoint recovery semantics,
and read-only PetriNet views. Core execution and optional host adapters share
Registry, workspace, permission and recovery authority.

Keep changes focused and preserve unrelated work. Select deterministic offline
checks by the changed boundary. Real provider/model calls require explicit
authorization and are not part of automatic tests. Keep English and Chinese
documentation aligned, including counterpart metadata and local links.

Commit only reviewed public product content. Do not commit credentials, provider
account details, private endpoints or profiles, Registry databases, raw experiment
logs or transcripts, internal notes or backlogs, local absolute paths, run
directories, caches, environments or build dependencies. Preserve required
public example fixtures, schemas, licenses and packaged static assets.

Public results must identify the tested source, inputs, execution conditions and
scorer. Retain failures, blocked and unrun scope and original score denominators.
Distinguish source candidates from released binaries and historical validation
from checks of the current revision. Review distributed bytes before publication;
a manifest or reviewer statement alone does not establish safety or execution.
Do not add automatic GitHub Actions push triggers.
