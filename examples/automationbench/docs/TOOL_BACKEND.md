# Tool backend boundary

[中文](TOOL_BACKEND_ZH.md) | [Design](../DESIGN.md)

`api_search`, `api_fetch`, and `base64_encode` are exposed as managed RPNH
plugin operations. A per-task broker serializes calls into one local
AutomationBench world. `api_fetch` is conservatively admitted as an external
write because the endpoint may read or mutate that world.

The business services are simulations provided by AutomationBench. They do not
contact production Gmail, Salesforce, Trello, or other SaaS accounts. Some
upstream tasks can use a separate ChatGPT business helper; those tasks were
excluded from the retained pilot so an unavailable helper was not represented
as a complete backend.

The external network activity reported for this pilot was the executor's model
provider route. Upstream rubric evaluation is programmatic and makes no model
call.
