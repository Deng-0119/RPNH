# Pinned DSH host

English | [中文](README_ZH.md)

Prepare the exact upstream checkout declared by the installed RPNH integration.
Set `DSH_SOURCE` and an authorized DSH-compatible execution selection. The
profile must retain the same route/model while leaving room inside DSH's 2 MiB
complete-frame limit.

This actual settled PetriNet comes from the checked DSH acceptance turn. Unlike
the one-transition MainSession hosts, it exposes DSH inspection, policy,
model/tool and finalization transitions registered by the host integration.

![DSH accepted-turn PetriNet](../assets/dsh-petrinet.png)

```bash
: "${DSH_SOURCE:?Set the pinned DSH checkout}"
: "${EXECUTION_CONFIG:?Set an authorized DSH-compatible selection}"
RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-dsh-example.XXXXXX")"
rpnh-dsh "$DSH_SOURCE" --execution "$EXECUTION_CONFIG" \
  --root "$RUN_ROOT" --task "$(cat task.txt)"
```

The task uses the shared RPNH provider adapter. DSH does not choose another
provider or exact model and does not turn this request into a workflow. It is
one registered DSH host turn; use its audit for the physical invocation count.
