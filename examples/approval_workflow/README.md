<!-- counterpart: README_ZH.md -->
# Controlled mock refund approval

[中文](README_ZH.md) · [Examples](../README.md)

Run a local, deterministic refund application with two independently controlled
browser clients and distinct original RPNH actors. A separate owner holds the
trusted frontdoor. No model, payment, refund service, production login, or public
deployment is involved. Amounts are integer minor units: `2500 USD` means USD 25.00.
The fixed synthetic case refers to `synthetic-order-001`; its original paid amount
and refund policy are immutable registered input material.

## Recorded Petri net

[![Original approval PN after two registered operations](figures/completed.png)](figures/completed.png)

This actual Viewer capture shows the original run after the candidate and mock
refund operations settled. Click the image for full size; see [capture provenance](figures/provenance.json)
and the [Viewer guide](../../docs/guides/viewer.md#controlled-input-examples).
The historical image is not a new execution or verification of later source edits.

## Run and reopen

From the product checkout, use an **existing** Python environment containing RPNH's
dependencies. Choose a new runtime directory inside your writable workspace:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m examples.approval_workflow.server \
  --workspace /absolute/writable/workspace \
  --run-dir /absolute/writable/workspace/refund-demo
```

The server binds only `127.0.0.1` and prints its URL and two temporary local
passwords. Keep the console and runtime private. These passwords are per-process
demo access controls, mapped by the server to persisted personal Core sessions;
they are not real external accounts. The server exposes no credential-minting or
role-switching endpoint. Runtime trees containing symlinks or hard links are
rejected.

1. Open **two separate browser profiles/contexts**. In the first, visit
   `/requester` and enter the printed `requester` credentials in the browser's
   Basic authentication prompt. Submit `2500`, `USD`, and a synthetic reason.
2. In the second, visit `/approver`, using the separate `approver` credentials.
   Refresh, expand the immutable candidate, and check its amount, policy/source
   references and exact run/task/net/transition/wait target. Approve or deny.
3. Refresh both clients. The result is read from the original Registry. Follow
   **Open original Viewer · same run** for the actual PN, checkpoints and firing
   history. The Viewer has a return link to the application.

Navigation never switches identity. Requester access to `/approver` is denied;
its state projection omits the approval candidate. An unrelated personal subject
cannot read the state, Viewer, or its APIs, and cannot submit either action.
Open the approver link in its already bound browser context. The UI supports
English and Chinese. Refresh is explicit; an uncertain POST is not automatically
retried. Repeating its unchanged command ID uses Registry replay.

Recorded views on this page show the original run while waiting and after completion.
Exact run/net/checkpoint references, capture conditions and limits are recorded
in [figure provenance](figures/provenance.json). These are runtime Viewer views;
`python -m examples.view_input_declaration approval_workflow --json` separately exports
the declared static PN. Add `--run-dir /absolute/writable/workspace/refund-demo/run`
to inspect the original Registry instead.

[![Original PN waiting for the exact approver input](figures/waiting.png)](figures/waiting.png)

The default decision deadline is 900 seconds; `--decision-seconds 60` selects a
shorter demo window. A state refresh triggers trusted owner expiry maintenance.
Waiting or accepted-but-unadmitted decisions past their deadline follow the
declared no-refund result. Already dispatched work stays under original recovery
authority. There is no business timer queue or application scheduler.

Stop the local server and reopen the **same** directory:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m examples.approval_workflow.server \
  --workspace /absolute/writable/workspace \
  --run-dir /absolute/writable/workspace/refund-demo --reopen
```

Use the newly printed temporary passwords in fresh browser contexts. Personal
identities, original task/run/net, input waits and immutable receipts persist in
their original Registries. Reopen with the same registered implementation/schema
inventory; changed registrations are rejected. Pending runs use native `resume_run`; terminal runs
open read-only without a new writer epoch. An exact registered completion is
settled through native recovery without calling its executor again. Missing
completion evidence fails closed and requires investigation; it is never
automatically replayed or labelled successful.

The declared PN is built by `contracts.module()`. It has two ordinary registered
operations and two private required-one external input ports:

```text
policy anchor + requester input → candidate.run → immutable candidate anchor
candidate anchor + exact approver decision → refund.run → registered mock result
```

Each external input uses `RunInputHost` with the exact anchor resource and a
Core-derived allowed principal. Both inputs consume their anchor; neither has
an internal producer or initial input token. The candidate binds the original
request, policy/source resources, requester session and exact target. The final
executor compares the complete candidate again. `completed` alone accompanies
`mock_refund: true`; deny and expiry produce `mock_refund: false`. A PN terminal
means the case was resolved, not that a refund was approved.

All pending, accepted, expired, completion and recovery facts remain in the
original Registry/PN. The Python lock only serializes access to the sole owner.
There is no side approval ledger, completed map, Branch review, or
`TaskControl.resume` approval shortcut. One immutable case is supported per
runtime; changed request bodies and stale/cross-run decisions are rejected.

The interaction contract is adapted from the [pinned Mastra customer-refund
example](https://github.com/mastra-ai/mastra/tree/4ca41b2135991ac744c3729bbb7d42bf731a8185/templates/template-customer-refund-agent)
at `4ca41b2135991ac744c3729bbb7d42bf731a8185`. This is a deterministic native
RPNH implementation, not a run of the upstream application or its authentication
stack. Real users, external account isolation, and financial effects are outside
this controlled demonstration. No source download or dependency installation is
required by these commands.

Focused validation (put temporary files inside your workspace):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m pytest \
  tests/test_approval_workflow.py -q -p no:cacheprovider \
  --basetemp /absolute/writable/workspace/approval-tests
```

The tests exercise the actual native socket/registered dispatch, distinct
sessions, altered/stale bodies, denied reads, replay/concurrency, expiry, reopen,
and independent HTTP clients. HTTP tests are separate from real browser evidence.
Raw test runtimes, first failures and console secrets stay outside the product
repository; do not publish Registry databases or browser credentials.
