# Optional GitHub OAuth account HOST

This distribution supplies the interactive account provider for RPNH's existing Registry/PN managed-account and file-descriptor transport. It is not installed by the core package.

Build from this directory with `python -m build`. Install the exact core `rpnh-harness==0.1.0rc5` wheel and this `rpnh-gh-interactive-oauth-host==0.0.8` wheel in the same environment. The provider implementation revision remains `oauth-candidate-2`; 0.0.8 changes package pairing, not consent behavior.

See the repository's `docs/guides/github-oauth-account.md` and Chinese counterpart for selected-account configuration and foreground confirmation. Do not supply credentials in configuration or copy them into reports. Only the selected github.com OAuth App account and declared read operations are supported; this does not grant broader GitHub permissions.

Historical HOST0.0.1 had scoped local real get_me/settlement/replay success. HOST0.0.2 had offline and credential-free PTY validation. Those records are not a new real-account test of 0.0.8. No registry publication or general API certification is claimed.
