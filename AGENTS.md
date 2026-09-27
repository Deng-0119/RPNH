# Repository working agreement

## Authoritative project

This repository, `Deng-0119/RPNH`, and its `main` branch are the authoritative
RPNH product line. By default, perform future inspection, implementation,
testing, documentation, and release work here. Verify the live `origin/main`
before work that depends on the latest state because this project is actively
updated.

Do not use `Deng-0119/rpnh-harness`, its historical feature branches, or local
worktrees derived from that repository as implementation or audit sources
unless the user explicitly names them. If the current worktree is not this
repository, stop and select the authoritative RPNH worktree before proceeding.

This repository contains the reusable RPNH harness. Keep it independent of
paper-specific workflows, private experiments, credentials, provider account
details, local absolute paths, and historical development-branch handoffs.

The product has one main line. Core Registry/PetriNet execution, the read-only
net viewer, and optional host adapters must coexist without duplicating provider,
workspace, permission, recovery, or Registry authority.

The supported user entry is the installed `rpnh` command. Preserve the basic
main session, independent task/workflow Registries, user-owned provider/exact-
model configuration, interruption/checkpoint semantics, and resource-aware
read-only PetriNet views.

Keep English and Chinese user documentation aligned. Use focused deterministic
offline tests while changing a boundary, then run the complete offline suite for
a release candidate. Real provider calls always require separate explicit
authorization and are never part of the automated test suite.

Do not add GitHub Actions `push` triggers. Do not commit generated provider
profiles, Registry databases, run directories, raw provider transcripts, build
environments, package caches, or viewer build dependencies.
