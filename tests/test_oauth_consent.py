"""Offline terminal-response and account-use regression tests; no real account."""
from contextlib import contextmanager
from io import StringIO
import os
import unittest
from unittest.mock import patch
import rpnh_gh_interactive_oauth_host as host
from cpn.plugins.account_credentials import AccountCredentialError, resolve_account_fd
from test_oauth_host import fixture


class TerminalBuffer(StringIO):
    # Keep rendered text available after _confirm closes its separate handles.
    def fileno(self):
        return 99

    def close(self):
        pass


@contextmanager
def terminal_response(answer, *, is_tty=True):
    reader, writer = TerminalBuffer(answer), TerminalBuffer()
    with patch.object(host, "open", side_effect=[reader, writer], create=True), \
            patch.object(host.os, "isatty", return_value=is_tty):
        yield reader, writer


class ConsentTests(unittest.TestCase):
    def test_yes_variants_authorize_only_the_selected_call(self):
        for answer in ("yes\n", "YES\n", " Yes \n"):
            with self.subTest(answer=answer):
                selection, scope, account, source, resolver = fixture()
                with terminal_response(answer), \
                        patch.object(host, "_gh_token", return_value=b"offline_fixture") as token, \
                        patch.object(host, "_identity", return_value="Alice"):
                    with resolve_account_fd(selection, scope, resolver) as lease:
                        fd = lease.take_fd()
                        try:
                            self.assertEqual(os.read(fd, 4096), b"offline_fixture")
                        finally:
                            os.close(fd)
                    token.assert_called_once()
                    with self.assertRaises(AccountCredentialError):
                        source.credential(account, selection, scope)
                    token.assert_called_once()

    def test_no_empty_eof_and_unrecognized_answers_do_not_fetch(self):
        for answer in ("no\n", "NO\n", "\n", "", "yes please\n", "ALLOW call-1\n"):
            with self.subTest(answer=answer):
                selection, scope, account, source, resolver = fixture()
                with terminal_response(answer), patch.object(host, "_gh_token") as token:
                    with self.assertRaises(AccountCredentialError):
                        with resolve_account_fd(selection, scope, resolver):
                            self.fail("denied answer must not yield a credential")
                    token.assert_not_called()

    def test_prompt_shows_scope_without_requiring_call_id_input(self):
        selection, scope, account, source, resolver = fixture()
        with terminal_response("no\n") as (_, writer):
            self.assertFalse(host._confirm(account, selection, scope, tool_name="get_me"))
        prompt = writer.getvalue()
        self.assertIn('"account": "Alice"', prompt)
        self.assertIn('"tool_name": "get_me"', prompt)
        self.assertIn('"call_id": "call-1"', prompt)
        self.assertIn('"principal_ref"', prompt)
        self.assertIn('"task_ref"', prompt)
        self.assertIn("GET /user", prompt)
        self.assertTrue(prompt.endswith("Allow this call? [yes/no] (default: no): "))
        self.assertNotIn("Type exactly ALLOW", prompt)

    def test_nonterminal_does_not_read_a_response(self):
        selection, scope, account, source, resolver = fixture()
        with terminal_response("yes\n", is_tty=False) as (reader, _):
            self.assertFalse(host._confirm(account, selection, scope))
            self.assertEqual(reader.tell(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
