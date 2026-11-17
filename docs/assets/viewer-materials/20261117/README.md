# Registry material captures — 2026-11-17

[English](README.md) | [中文](README_ZH.md) · [User guide](../../../guides/viewer-materials.md)

These nine image pairs come from one recorded real Chromium session on WSL/Linux,
using synthetic local material fixtures and the original Owner, Registry and HTTP
implementation. Their capture-source revision is `851b721dc22ac9796bca5c46e9c6e50f9163f960`.
The six-phase reading/editing/navigation window completed in 101.51 seconds.
No real model call was made in that window.

Raw PNGs are the original captures. Annotated PNGs are also retained unchanged:
the original driver drew red frames and numbered labels around actual DOM controls
before capturing them. Labels explain the controls; they do not change the
displayed result. The demonstration IDs are synthetic Registry identities.

## Images and observed scope

| Capture | Annotated image / original | Numbered controls | Observed scope |
|---|---|---|---|
| S01 — resources | [Annotated](S01-resources.annotated.png) · [Raw](S01-resources.raw.png) | 1 source/type; 2 page filter; 3 refresh; 4 resource group | Loaded resource page and exact version groups. |
| S02 — exact record | [Annotated](S02-exact-record.annotated.png) · [Raw](S02-exact-record.raw.png) | 1 exact record; 2 original fields; 3 close | Record and declared references; body access is separate. |
| S03 — position history | [Annotated](S03-position-history.annotated.png) · [Raw](S03-position-history.raw.png) | 1 history; 2 return; 3 refresh; 4 original binding | One observed position's binding history. |
| S04 — complete read | [Annotated](S04-complete-read.annotated.png) · [Raw](S04-complete-read.raw.png) | 1 reading panel; 2 received text; 3 edit received text | Complete text received and original read confirmed. |
| S05 — draft and result | [Annotated](S05-draft-and-result.annotated.png) · [Raw](S05-draft-and-result.raw.png) | 1 independent draft; 2 prepare/save/close; 3 original receipt | V1 revised to published V2; independent D2 remains unsubmitted. |
| S06 — disablement | [Annotated](S06-disable.annotated.png) · [Raw](S06-disable.raw.png) | 1 disable panel; 2 check actions; 3 status | `management_not_configured`; no disablement executed in this capture. |
| S07 — carriers | [Annotated](S07-carriers.annotated.png) · [Raw](S07-carriers.raw.png) | 1 carrier panel; 2 relationship/state controls | `action_unavailable`; no successful carrier inspection established by this image. |
| S08 — cleanup | [Annotated](S08-cleanup.annotated.png) · [Raw](S08-cleanup.raw.png) | 1 single-carrier panel; 2 check actions; 3 status | `action_unavailable`; no physical cleanup executed in this capture. |
| S09 — narrow navigation | [Annotated](S09-narrow-navigation.annotated.png) · [Raw](S09-narrow-navigation.raw.png) | 1 source/type toggle; 2 page filter | Real 390 × 844 viewport, Chinese labels. |

S01–S08 use a 1440 × 1050 viewport. These are viewport captures, not a claim that
the whole page fits on one screen.

## Later terminal-location verification

The first terminal-location attempt on `851b721` returned HTTP 409
`target_unavailable`. Its failure was retained. A subsequent fix at
`3354e410b04e3f9f2da5a14ae13c63b0385ef2d2` passed three installed permission
regressions and one original Owner/HTTP/browser bind→unbind journey. The latter
contained ten HTTP 200 responses and exactly two management transitions;
business generation and original business result stayed unchanged. A refreshed
catalog and Registry confirmed the final removed binding.

The nine images above were **not** recaptured on `3354e410`. In particular, they
must not be relabeled as a final-package journey or as deletion/cleanup evidence.
The Viewer source fixes were later included in public source
[`f4eb4ee2`](https://github.com/Deng-0119/RPNH/commit/f4eb4ee2f77fae79d162644765ebd701d3c576c0).
That integration does not change these captures' original source or prove
installation of a later wheel/npm package.

User visual sign-off, other native platforms and complete disable/cleanup paths
are not established by this capture set. [Provenance metadata](provenance.json)
records the image identities and scope without local paths or private task links.
