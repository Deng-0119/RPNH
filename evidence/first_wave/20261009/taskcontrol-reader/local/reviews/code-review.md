# Independent bounded H2a review

Reviewer profile: reviewer-v1; separate software agent; read-only; no runtime tests.
Base: 715468dab0b1bea07d7e94a7aa0606eaf194365c; target: combined H1 plus exact frozen H2a five paths.

No blocker or confirmed correctness defect found in the bounded H2a-over-H1 review.

- cpn/rpnh/task_control.py:577,622: both consumers recheck the same captured cut after cumulative counts; result also finishes JSON decoding first.
- cpn/rpnh/task_control.py:604: current authority selects the result; running/stopped generations cannot return an older terminal.
- cpn/rpnh/registry/run_authority.py:165: omitted descriptor budgets default to registered size; explicit budgets retain precedence. No 4 MiB cap. The underlying reader rejects enlarged/shortened files rather than returning truncated output.
- cpn/rpnh/task_control.py:555,598: both open read-only cores. Status retains independent Registry/process/socket observations, matching both architecture documents.

Validation gap at review time: no tests executed, as instructed; mandatory runtime gates remain with the parent. The new test file was untracked and absent from ordinary git diff; its contents were reviewed directly.

Parent acceptance: cited source locations and actual diff checked; runtime acceptance is recorded separately after all batches finish.
