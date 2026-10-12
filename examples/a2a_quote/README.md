<!-- counterpart: README_ZH.md -->

# Controlled external-result quote

[中文](README_ZH.md) · [Examples](../README.md) · [Viewer guide](../../docs/guides/viewer.md)

This local application admits a selected synthetic external result to one exact
RPNH input wait, then publishes a registered mock hotel quote. The holder page
and original Viewer observe the same Registry and Petri net. A custom Python
HTTP adapter supplies deterministic status fixtures; no model, external merchant,
booking or payment is called.

## Recorded Petri net

[![Original quote PN after one registered operation](figures/completed.png)](figures/completed.png)

This actual Viewer capture shows one settled quote in the original run. Click
for full size; [capture provenance](figures/provenance.json) records its exact
net/checkpoint and historical source window. It is not a new execution or
verification of later source edits. Use the [same-run Viewer commands](#run-and-reopen)
to inspect a run created by this application.

The recorded controlled browser selected `trusted25`, producing USD 150.00 with
one firing, one input receipt and zero model calls. `trusted25`, `untrusted50`
and `expired` are status fixtures, not signed vouchers. A completed undiscounted
quote is a business outcome, not successful proof verification. See
[scope and provenance](#source-and-validation) for transport and identity limits.

## Run and reopen

Use an existing environment that already runs this checkout. Run commands from
the product repository root. Choose new, local writable directories outside the
product tree; do not point them at shared runtimes. The application rejects
symlink traversal and existing hard-linked runtime files.

```bash
# Product repository cwd; its parent is the explicitly chosen writable workspace.
export A2A_DEMO_WORKSPACE="$(cd .. && pwd -P)"
export A2A_DEMO_RUN="$A2A_DEMO_WORKSPACE/a2a-local-demo-01"
export PYTHONDONTWRITEBYTECODE=1
# TMPDIR must already point to a task-owned temporary directory inside that workspace.
python -m examples.a2a_quote.server \
  --workspace "$A2A_DEMO_WORKSPACE" --run-dir "$A2A_DEMO_RUN" \
  --proof-seconds 900 --port 0
```

Open the printed `http://127.0.0.1:PORT/`. This controlled loopback frontdoor
prebinds every browser to **one holder**; it has no role selector, password,
credential endpoint or external login. The holder, local fixture adapter and
owner have distinct original Registry principals/sessions. Only the adapter's
verifier session may submit proof evidence to the native wait. This does not
establish multi-tenant isolation or equate a credential subject with a user.

1. Inspect the hotel, immutable request reference, wait generation and expiry.
   The original PN is already waiting; no quote has fired.
2. Choose **Trusted25** for a synthetic 25% discount: USD 200.00 becomes
   USD 150.00. **Untrusted50**, **Expired fixture** and **Decline** each produce
   one USD 200.00 quote with the corresponding explicit noneligible verdict.
3. Before selection, **Replace waiting evidence request** opens generation +1 via
   native wait supersession. The request/session/challenge references change;
   callbacks for the old wait are rejected.
4. **Refresh state** observes the original Registry and processes due native
   expiry. An elapsed request yields an undiscounted quote. There is no separate
   timer, scheduler or background business ledger.
5. Follow **Open original Viewer · same run**, inspect the Petri/flow views and
   firing records, then use **Return to quote app**. Use the language button for
   English or Chinese. A network error leaves completion unconfirmed; refresh
   original state before retrying.

Stop with Ctrl-C, then reopen the **same app directory**:

```bash
python -m examples.a2a_quote.server \
  --workspace "$A2A_DEMO_WORKSPACE" --run-dir "$A2A_DEMO_RUN" --reopen --port 0
```

Waiting and accepted-before-admission states use plain `resume_run`, preserving
the original run/net/association and fencing old writers. Accepted evidence that
expires before admission is replaced atomically by the configured native expiry
input. Terminal reopen is a read-only projection: no writer gateway, new
checkpoint or repeated quote. Missing persisted association blocks continuation;
the host does not invent a mapping or resubmit a proof. An interrupted startup
without a published wait remains blocked by native recovery; preserve it and
start a separately identified new request in a new directory.

The app's `--run-dir` is its parent directory; the **actual native Registry is
`$A2A_DEMO_RUN/run`**. The standalone actual Viewer uses that child:

```bash
python -m examples.view_input_declaration a2a_quote \
  --run-dir "$A2A_DEMO_RUN/run"
```

Keep the whole app directory in place for reopen; it includes the run and
original personal Registries. They are local runtime evidence, not files to
commit or publish.

## Exact association and transport

The immutable `run_input_wait/v1` expiry input carries the nonsecret association:
original run/task/net, private input wait and generation, exact anchor request
resource, assistant and merchant task/context pairs, native verifier principal
and HostSession, synthetic verification-session/request/challenge references,
and expiry. The expiry payload doubles as the explicitly declared no-discount
continuation. The host compares the **whole association**; matching context alone
never grants admission. The required-one evidence port is private and has no
producer. Its anchor resource is exactly `InputWaitSpec.binding_ref`.

The browser sends only a fixture selector, exact association and retry command
ID to `POST /api/select`. The host computes minimal fixture evidence and submits
through `RunInputHost` using its own verifier session. Browser `approved:true`,
actor, session or verdict fields are rejected. There is no HTTP callback route
for arbitrary verifier output. The native input guard independently checks the
principal, binding, deadline, checkpoint and one-use wait. The original
Orchestrator admits and settles the quote once. UI state is a Registry projection;
there are no pending/completed maps, ready queues or business ledgers.

`GET /api/state` returns state, association, result, checkpoint, evidence-stage
labels and observed firing/receipt/model counts. `POST /api/renew` accepts only
`{"association": <exact current association>}`. POST requires the exact local
Origin; Host and cross-site checks also cover Viewer routes. Python integration:
`A2AQuoteApp(directory, workspace=..., reopen=False)` and
`A2AQuoteServer(app, port=0)` expose `server.origin` and `serve_forever()`.

Only nonsecret references, verdicts and fixture labels enter the Registry and
Viewer. There are no credentials, presentations, nonce bytes, secret URIs or
planner/model calls. Synthetic request references are not bearer credentials.

## Source and validation

The transport is custom local HTTP, not tested A2A interoperability. No A2A SDK,
Agent Card, JSON-RPC/SSE exchange, Credo wallet, OID4VP flow, signature or trust-chain
verification is established by this example. The `a2a_quote` module name, classes,
schema identifiers and command arguments are retained for compatibility with the
original recorded runs; they do not claim protocol conformance.

The interaction is derived from the supplied pinned
[A2A sample at 6603ba3](https://github.com/a2aproject/a2a-samples/tree/6603ba3f2c31a7ef33e70b9d8b5b5f8be42ac9a3/samples/js/extensions/oid4vp-in-task-auth).
This is a new deterministic RPNH adapter: it does not port the sample's in-memory
context maps, run its model-based intent path, or claim its Credo protocol ran.
The original RPNH Registry/PN remains the only durable execution authority.

The targeted tests are in [test_a2a_quote.py](../../tests/test_a2a_quote.py).
They cover trusted/untrusted/expired/deny fixtures; all association dimensions;
two requests sharing context; changed challenge after supersession; concurrent
replay; native wrong-principal denial; deadline/late callback; original-run
reopen and old writer fencing; terminal head/epoch stability; missing mapping;
accepted-before-admission expiry; and loopback HTTP with actual Viewer routes.
Use `-p no:cacheprovider` and a **new task-local** `--basetemp` for each attempt.
HTTP tests explicitly bypass inherited proxies only for the local test client.

The original waiting capture below and the completed capture above come from
the same Registry/PN. Their pixels are unchanged; [provenance](figures/provenance.json)
records the capture conditions. They are runtime views, not declaration diagrams
or reused approval screenshots. Historical native/browser checks do not certify
a later combined source candidate.

[![Original quote PN waiting for synthetic fixture evidence](figures/waiting.png)](figures/waiting.png)
