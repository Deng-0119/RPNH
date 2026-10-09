# Complete v5 stage evidence

[Final results (Chinese)](RESULTS_ZH.md) · [Result manifest](result-manifest.json)
· [Parent final gate](final-gate-summary.json) · [Publication inventory](publication-manifest.json)

Stage records, including original failures and inconclusive attempts:
[core](stages/core/) · [RSI](stages/rsi/) · [Codex](stages/codex/)
· [DSH](stages/dsh/) · [OpenCode](stages/opencode/).
Received failures/reviews remain under [candidate evidence](received/candidates/);
the inventory explicitly lists malformed originals, empty files and NUL records.
Those are historical evidence, never repaired into a pass. Escaped JSON views are
companions, not replacements. Only explicitly nominated final control JSON and
accepted JUnit files must parse; historical XML/JSON may remain malformed.
Accepted means reviewed for publication; parseable FAIL results, including native
g7, remain valid evidence and are never relabeled PASS.

[Received bundle metadata](received/bundle/) includes archive SHA/size/member
metadata; archives, source snapshots, databases, private profiles, package work
copies and caches are excluded. Selected root patches are candidate evidence;
historical patches are labeled separately in the inventory. No patch is applied,
no candidate code is merged, and no product default changes here. Candidate source
copies are deliberately omitted: the selected patches carry the changed paths.

[Local commands/helpers](local/) and [publication helpers](publication-tools/)
record original execution sources; they require local path configuration and are
not advertised as portable. Approved local prefix substitutions are longest-first:
`/mnt/c/Users/64681/Downloads` → `<INPUT_DIRECTORY>`, `/home/deng123/RPNH` →
`<WORKSPACE>`, `/home/deng123` → `<USER_HOME>`. Parseable XML uses escaped
replacement text; malformed XML keeps its byte structure and its malformed label.
Every change has original/included hashes, sizes, byte offsets and line ranges.
Received root JSON controls and exact selected patches remain frozen; a private
prefix there aborts instead of silently rewriting the received control.

The parent supplied the final gate and privacy review. Regex scanning is a review
aid, not a secrecy certificate. Consult the final results for status and limitations;
this index invents no test totals or success claims.
