# Independent H7 lowering review

Read `REVIEW.md` for the scoped decision, findings and unsupported boundaries. `TEST_COUNTS.json` and `REVIEW_IDENTITY.json` contain the final deduplicated test and byte identities.

- `reviewed-source/`: immutable reviewed product snapshot, excluding transient Python/pytest caches from its hash contract.
- `reconstructed-source/`: independent application of the final author patch to the exact input.
- `tests/`: independent D0 tests; the inherited private native-evidence fixture is only test injection.
- `evidence/`: original logs, JUnit results, early failure snapshots and source manifests.
- `inputs/`: frozen design/scope/author identity and patch inputs.

No author source was changed. No live native/model/client/API/installation/remote code-push action was performed.
