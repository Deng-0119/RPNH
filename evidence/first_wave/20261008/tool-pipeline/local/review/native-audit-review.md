No actual issue found in this focused acceptance follow-up. Initial review P2 gap 1 is closed by the raw event/claimed-lineage evidence. Overlap/barrier matching and task D no-new-dispatch are supported. Initial `code-review.md` remains unchanged; supplementary gaps 2/3 were not reviewed.

Checked `audit_native_evidence.py`, `native-evidence-audit.json`, the actual controlled/standard/readback exports and controlled observations, readback before/after logs and protocol. Also read the audit script's direct third input, `.p26/c/export/evidence.json`, to verify the requested 36-output total rather than relying on its summary. All `.p26` paths below are relative to `<WORKSPACE>`; script/log/protocol paths are relative to the task directory.

The script joins exact firing refs to admission, transition Start, operation Start, completion and settlement (`audit_native_evidence.py:29`), resolves claimed token versions to resource refs (line 43), and checks each output's parents/derived_from, binding and producer (lines 49–59). Independent read-only JSON checks confirmed its recorded rows against all raw exports: **30 firings, 36 distinct output resource versions, exactly 12 outputs per run**, with no skipped or duplicate output. Raw publication metadata matches resource metadata; operation-result output refs also match the completion outputs. All branch dependency edges settle before successor admission, and peak active firing count is two in each run.

Concrete ordinals from `.p26/a/tool-pipeline-controlled0/rebuilt-export/evidence.json`:

| Firing | Admission | Transition Start | Operation Start | Output publication | Completion recorded | Completed settlement |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
| read_tariff | 312 | 313 | 348 | 543, 547 | 551 | 556 |
| read_usage | 352 | 353 | 388 | 390, 394 | 398 | 403 |
| check_usage_input | 421 | 422 | 457 | 459 | 463 | 468 |
| normalize_usage | 483 | 484 | 519 | 521 | 525 | 530 |
| check_tariff_input | 574 | 575 | 610 | 612 | 616 | 621 |
| normalize_tariff | 636 | 637 | 672 | 674 | 678 | 683 |
| join_intervals | 698 | 699 | 748 | 750 | 755 | 760 |
| compute_cost | 775 | 776 | 811 | 813 | 817 | 822 |
| validate_report | 837 | 838 | 901 | 903 | 909 | 914 |
| publish_report | 929 | 930 | 965 | 967 | 971 | 976 |

“Completion recorded” is `registered_operation_completion_recorded/v1` (the audit JSON calls this `success_recorded`); actual successful settlement is separately confirmed by `transition_firing_settled/v1`, business_outcome=completed and the operation result. These are not conflated in this acceptance decision.

Standard `.p26/b/preserved-export/evidence.json` and rounding `.p26/c/export/evidence.json` independently match their complete audit tables. Examples in both: read_usage 352→353→388→[394,402]→408→432; normalize_usage 614→615→650→656→662→683; join 698→699→748→750→755→760; publish 929→930→965→967→971→976. Their final totals are respectively 1.70 and 0.02 CNY.

Controlled observations match the raw active-firing set at **389**: read_usage `7c01686a20975411aa513943b5717d2e` and read_tariff `bda2d85bc57a5510bdaba7bba85faf54` (both IDs carry the `transition_firing_version:` prefix). Distinct worker entries are recorded, with both operation Starts preceding this barrier and neither output yet published. At **542**, usage read/check/normalize have settled at **403/468/530**; normalized resource `resource_version:7a424a6d658f558384c1753d5df82eb8` was published at **521** and matches the snapshot. Only tariff remains active, settling at **556**. Join is disabled in the snapshot and is first admitted at **698**, after tariff normalization settles at **683**.

Task D: preserved and readback evidence are deeply equal for every stable field, including all resources/source versions, final candidate/validation refs, lineage, checkpoint, firings, events and projection. Only original run-added stop_reason/transport are absent from readback. Both stdout counter logs independently match raw events: **max ordinal=1001, event count=1001, dispatch reservations=10, execution Starts=10, model counts=[0,0]**. Last dispatch is **932**, last operation Start **965**; no additional event or dispatch appears in readback. The protocol records the standard process finished at 13:15:41.973303 UTC, old export moved at 13:17:31.236889 UTC, and a new CLI process receiving only the run directory and fresh output path. The old export path is still absent; preserved evidence remains available.

Scope: standard/rounding exports explicitly identify native_owner_socket. The controlled rebuilt export omits live-only transport metadata, so its native designation rests on the parent's native-suite provenance, not a new transport test here. This review performed only static reading and JSON comparisons; it did not execute the audit script's writing main(), any product runtime, supplementary probes, provider/network calls or delegation. Only this report was created. No required correction remains within this follow-up's scope.
