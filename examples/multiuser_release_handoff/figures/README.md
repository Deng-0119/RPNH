# Actual native Viewer gallery

[中文](README_ZH.md) · [Example](../README.md) · [Image provenance](provenance.json)

Start with the role PN. Every image links to its original full resolution. These are unchanged native Viewer PNGs from the original completed episode's read-only capture; no graph was redrawn or relabeled. Screenshots retain technical run/checkpoint identities and original observation times, which are historical evidence rather than access credentials. There is no distributed Registry database or saved-frame replay server in this curated gallery.

| Registry | Full PN | Nodes / arcs | Child execution instances | Final observation head |
|---|---|---:|---:|---:|
| Operations | [Agent responsibilities](operations-petri.png) | 6 / 9 | 6 | 6125 |
| Developer | [Agent responsibility](developer-petri.png) | 3 / 3 | 2 | 1828 |
| QA | [Initial read and revised-release confirmation](qa-petri.png) | 6 / 9 | 5 | 4645 |
| Support | [Initial read, incident and confirmation](support-petri.png) | 6 / 9 | 6 | 5505 |
| Shared Source | [Complete resource-control panorama](source-petri.png) | 32 / 36 | 0 | 4351 |

The first four are independent agent business PNs. Source controls canonical resource reads, leases, writes and messages; it is not an agent PN. Its full panorama is intentionally retained without cropping away branches. For readable close inspection, open [Source write-node details](source-write-node.png) and [Source lease-resource details](source-resource.png). The image's original whitespace/layout is preserved.

[![Operations agent PN](operations-petri.png)](operations-petri.png)

## Source panorama and historical cuts

[![Shared Source resource-control PN, full original panorama](source-petri.png)](source-petri.png)

[Full resolution](source-petri.png) · [Write detail](source-write-node.png) · [Resource detail](source-resource.png) · [Actual initial historical cut](source-initial.png) · [Actual last historical cut](source-last.png)

History is an observation of committed checkpoints, not an execution or rollback control. Current and historical cuts have different observation semantics. Developer and QA retain their prior terminal state; they were not reopened in run02. Optional completeness of all requested older history cuts remains UNKNOWN.

## An execution PN beneath an exact agent firing

[![Actual workspace-finalization execution PN](operations-execution-finalization.png)](operations-execution-finalization.png)

This is the original RPNH workspace-finalization execution definition attached beneath a business firing in the operations Registry. It is not a fifth role, a renamed release process, or a replacement for the agent PN. [Agent step inspector](operations-node.png) is a separate detail view.
