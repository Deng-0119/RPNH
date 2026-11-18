"""Optional Linux interactive HOST adapter. Never an unattended grant service.

Original Registry/PN selects the account, admits the call and owns replay.
This module supplies only explicit human current-use consent and gh's account
credential source to the existing provider/FD ABI. No grant database is added.
"""
from __future__ import annotations
import http.client
import json
import os
import re
import subprocess
import threading
from cpn.plugins.account_credentials import AccountCredentialError, ResolvedAccountFD
from cpn.plugins.account_bindings import AccountBindingRequest
from cpn.plugins.github_oauth_provider import (
    GitHubOAuthAccountProvider, GitHubOAuthCredential, GitHubOAuthResolverFactory,
)

_TTY_LOCK = threading.Lock()


def _confirm(account, selection, scope, *, tool_name=None):
    # Serialize this process's threads only; multiple HOST processes need distinct terminals.
    # No stdin fallback: worker stdin can be model/tool-controlled text.
    with _TTY_LOCK:
        try:
            with open('/dev/tty', 'r', encoding='utf-8') as reader, \
                    open('/dev/tty', 'w', encoding='utf-8', buffering=1) as tty:
                if not os.isatty(tty.fileno()):
                    return False
                detail = {'account': account.account_id, 'service': account.service_id,
                          'host': account.host, 'selection': selection.to_dict(),
                          'call_scope': scope.to_dict(), 'tool_name': tool_name}
                tty.write('\nRPNH LOCAL ACCOUNT CONSENT (one dispatch only)\n')
                tty.write(json.dumps(detail, ensure_ascii=True, indent=2) + '\n')
                tty.write('Allow gh credential use for GET /user identity check and the '
                          'selected external_read MCP tool? This does not expand GitHub permissions.\n')
                tty.write('Allow this call? [yes/no] (default: no): ')
                tty.flush()
                return reader.readline(8192).strip().casefold() == 'yes'
        except (OSError, UnicodeError):
            return False


def _gh_token(account):
    executable = os.environ.get('RPNH_GH_EXECUTABLE', '')
    if not os.path.isabs(executable) or not os.path.isfile(executable):
        raise AccountCredentialError('account_use_unavailable')
    # Ambient tokens override stored accounts in gh. They must not reach this call.
    env = {key: os.environ[key] for key in ('HOME', 'PATH', 'LANG', 'LC_ALL',
           'GH_CONFIG_DIR', 'XDG_CONFIG_HOME', 'DBUS_SESSION_BUS_ADDRESS',
           'XDG_RUNTIME_DIR') if key in os.environ}
    env.update(GH_PROMPT_DISABLED='1', GH_NO_UPDATE_NOTIFIER='1')
    try:
        result = subprocess.run([executable, 'auth', 'token', '--hostname',
                                 account.host, '--user', account.account_id],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env, timeout=30,
                                check=False)
        token = result.stdout.rstrip(b'\n')
        if (result.returncode != 0 or not token or len(token) > 4096
                or any(byte < 33 or byte > 126 for byte in token)
                or not token.startswith(b'gho_')):
            raise AccountCredentialError()
        return token
    except (OSError, subprocess.SubprocessError):
        raise AccountCredentialError() from None


def _identity(token):
    # Identity verification is not the MCP business call, nor a permission grant.
    # Exact host, TLS, no redirect, no ambient proxy, no logged headers/body.
    connection = http.client.HTTPSConnection('api.github.com', timeout=20)
    try:
        connection.request('GET', '/user', headers={
            'Authorization': 'Bearer ' + token.decode('ascii'),
            'Accept': 'application/vnd.github+json', 'User-Agent': 'RPNH-gh-host/0.1',
            'X-GitHub-Api-Version': '2022-11-28'})
        response = connection.getresponse()
        body = response.read(16385)
        if response.status != 200 or len(body) > 16384:
            raise AccountCredentialError()
        login = json.loads(body)['login']
        if type(login) is not str:
            raise AccountCredentialError()
        return login
    except Exception:
        raise AccountCredentialError() from None
    finally:
        connection.close()


class InteractiveGitHubSource:
    """Process-local once-only consent handoff, not persistent authorization.

    A transient ticket links the two required callbacks for the identical call.
    It cannot authorize another call, task, account or process. Original managed
    dispatch/replay controls when these callbacks can be reached.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._tickets = set()
        self.tool_names = {}

    @staticmethod
    def _key(account, selection, scope):
        return json.dumps([account.host, account.service_id, account.account_id,
                           selection.to_dict(), scope.to_dict()], sort_keys=True)

    def current_use(self, account, selection, scope):
        key = self._key(account, selection, scope)
        with self._lock:
            self._tickets.discard(key)
        permitted = _confirm(account, selection, scope,
                             tool_name=self.tool_names.get(scope.registration_key))
        if permitted is True:
            with self._lock:
                self._tickets.add(key)
        return permitted is True

    def credential(self, account, selection, scope):
        key = self._key(account, selection, scope)
        with self._lock:
            if key not in self._tickets:
                raise AccountCredentialError('account_use_denied')
            self._tickets.remove(key)
        token = _gh_token(account)
        if _identity(token).casefold() != account.account_id.casefold():
            raise AccountCredentialError('account_resolver_mismatch')
        read_fd, write_fd = os.pipe()
        try:
            if os.write(write_fd, token) != len(token):
                raise AccountCredentialError()
            os.close(write_fd); write_fd = None
            owned_fd, read_fd = read_fd, None
            lease = ResolvedAccountFD(selection, scope, owned_fd)
            return GitHubOAuthCredential(account, lease)
        finally:
            if write_fd is not None:
                os.close(write_fd)
            if read_fd is not None:
                os.close(read_fd)


def factory(*, owner, catalog, node_id, account_selections):
    """Installed original rpnh.account_providers entry point, opt-in only."""
    node = 'dsh' if node_id is None else node_id
    source = InteractiveGitHubSource()
    providers = []
    for name, selection in account_selections.items():
        declaration = catalog.declaration(name)
        _plugin, operation = catalog.binding(name)
        if declaration is not None:
            source.tool_names[declaration.registration_key] = name
        if (operation.effect != 'external_read' or selection.service_id != 'github'
                or re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?',
                                selection.account_id) is None):
            raise AccountCredentialError('account_resolver_mismatch')
        providers.append(GitHubOAuthAccountProvider(
            AccountBindingRequest(node, name, selection.service_id,
                                  selection.account_id, selection.binding_revision, slot="github_oauth/v1"),
            host='github.com', current_use_check=source.current_use,
            credential_source=source.credential))
    return GitHubOAuthResolverFactory(providers)(owner=owner, catalog=catalog,
                node_id=node_id, account_selections=account_selections)


factory.__rpnh_plugin_implementation__ = {
    'schema_version': 'rpnh/plugin_implementation/v2',
    'implementation_id': 'rpnh.local.gh_interactive_oauth', 'revision': 'oauth-candidate-2',
    'handler': 'rpnh_gh_interactive_oauth_host.factory'}

