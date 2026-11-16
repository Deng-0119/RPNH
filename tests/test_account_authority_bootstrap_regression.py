"""Narrow regressions against the 14532 account/managed interfaces.

Input cases retain a real original Owner, Start and acknowledged binding input.
Worker cases use the existing typed synthetic lease fixture and real spawn/FD
transport; they do not grant account authority or contact a provider. No legacy
handler digest path is needed by the new probe.
"""
from __future__ import annotations

import builtins
from dataclasses import replace
from multiprocessing import resource_tracker
import os
from pathlib import Path
import sys
import time
import tempfile
import unittest
from unittest.mock import patch

from cpn.plugins import account_credentials as bridge, worker
from cpn.plugins.api import canonical, implementation_identity, plugin_implementation
from cpn.plugins.managed_tools import ManagedPluginInvocationService
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.resources import SettledPetriInputArtifact
from test_managed_account_service_unittest import account_world
from test_managed_account_worker_unittest import _lease


_VARIABLE = "GITHUB_PERSONAL_ACCESS_TOKEN"
_SENTINEL = "RPNH_ACCOUNT_BOOTSTRAP_SENTINEL"
_IMPORTED_PRIMARY = _VARIABLE in os.environ
_IMPORTED_SENTINEL = _SENTINEL in os.environ


@plugin_implementation("tests.account_authority_bootstrap_probe", "1")
def account_bootstrap_probe(context, arguments):
    return {
        "imported_primary": _IMPORTED_PRIMARY,
        "imported_sentinel": _IMPORTED_SENTINEL,
        "runtime_primary": _VARIABLE in os.environ,
        "selected_credential": context.child_environment.get(_VARIABLE) == "synthetic-account-a",
    }


def worker_packet(credential):
    return {
        "context": {"config": {}, "resources": (), "operation_id": "fixture/read",
                    "invocation_id": "synthetic_invocation", "firing_id": "synthetic_firing",
                    "call_id": credential.scope.call_id},
        "arguments": {}, "max_result_bytes": 16384,
        "implementation": implementation_identity(account_bootstrap_probe, identity_version="v2"),
        "account_selection": credential.selection.to_dict(),
        "account_scope": credential.scope.to_dict(),
    }


class AccountInputAuthorityRegressionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="rpnh-account-authority-")
        self.addCleanup(self.directory.cleanup)
        self.calls = []
        self.owner, self.execution, self.selection, self.service, self.managed = account_world(
            Path(self.directory.name),
            current_use_check=lambda *_: self.calls.append("use") or True,
            credential_callback=lambda *_: self.calls.append("secret"))
        self.item, = self.execution.operation.inputs

    def altered_execution(self, artifact):
        return replace(self.execution, operation=replace(self.execution.operation,
            inputs=(replace(self.item, artifact=artifact),)))

    def assert_no_claim(self, service, execution, call_id):
        _key, refs = service._refs(execution, call_id)
        self.assertIsNone(self.owner._core.event_store.object_row(refs["started"].resource_version_id))
        self.assertEqual(self.calls, [])

    def test_same_length_supplied_body_cannot_choose_another_account(self):
        selected = replace(self.selection, account_id="account_b")
        payload = canonical(selected.binding_document())
        self.assertEqual(len(payload), len(self.item.artifact.payload))
        altered = self.altered_execution(replace(self.item.artifact, payload=payload))
        resolver = bridge.InstalledAccountResolver(selected,
            lambda *_: self.calls.append("secret"),
            current_use_check=lambda *_: self.calls.append("use") or True)
        service = ManagedPluginInvocationService(self.owner, self.service.kernel,
            self.service.repository, self.managed, account_resolvers={"account_read": resolver})
        with self.assertRaisesRegex(bridge.AccountCredentialError, "account_binding_unavailable"):
            service.prepare("account_read", execution=altered, call_id="same-length", arguments={})
        self.assert_no_claim(service, altered, "same-length")

    def test_settled_carrier_requires_original_receipt_header_and_start_before_read(self):
        # Deliberately constructed over current real facts. This is not a claim
        # that a settled/resumed operation has been executed by the fixture.
        item = self.item

        def carrier(mode):
            payload, resource, receipt = item.artifact.payload, item.artifact.resource, item.artifact.receipt
            start = self.execution.start_event_id
            if mode == "short_count_and_header":
                payload = payload[:1]
                receipt = replace(receipt, positive_byte_count=1)
                resource = replace(resource, header=replace(resource.header, byte_size=1))
            elif mode == "wrong_header":
                resource = replace(resource, header=replace(resource.header, byte_size=1))
            elif mode == "wrong_receipt":
                receipt = replace(receipt, consumer_evidence=receipt.consumer_evidence + ":forged")
            elif mode == "wrong_start":
                start = new_id("event")
            return SettledPetriInputArtifact(payload=payload, resource=resource, receipt=receipt,
                source_invocation_ref=self.execution.operation.canonical.context.invocation_ref,
                source_firing_ref=self.execution.operation.firing.transition_firing_ref,
                operation_start_event_id=start)

        original_read = type(self.service.kernel)._read_firing_registered
        for mode in ("short_count_and_header", "wrong_header", "wrong_receipt", "wrong_start"):
            reads = []

            def observe(kernel, context, ref, *args, **kwargs):
                if ref == item.resource_ref:
                    reads.append(ref)
                return original_read(kernel, context, ref, *args, **kwargs)

            altered = self.altered_execution(carrier(mode))
            with self.subTest(mode=mode):
                with patch.object(type(self.service.kernel), "_read_firing_registered", observe):
                    with self.assertRaises(bridge.AccountCredentialError):
                        self.service.prepare("account_read", execution=altered,
                            call_id=mode, arguments={})
                self.assertEqual(reads, [])
                self.assert_no_claim(self.service, altered, mode)

        full = self.altered_execution(carrier("full_original"))
        scope = bridge.prepare_account_scope(self.service, full,
            self.managed.declaration("account_read"), self.selection, call_id="full-control")
        self.assertEqual(scope.principal_ref, self.selection.principal_ref)
        self.assertEqual(scope.execution_ref.ref, self.execution.operation_execution_lease_ref)


@unittest.skipUnless(os.name == "posix", "installed account transport requires POSIX spawn")
class AccountBootstrapRegressionTests(unittest.TestCase):
    def test_account_import_does_not_inherit_parent_environment(self):
        credential = _lease()
        self.addCleanup(credential.close)
        fd = credential._fd
        with patch.dict(os.environ, {_VARIABLE: "ambient-other-account", _SENTINEL: "ambient-marker"}):
            output = worker.execute_worker(account_bootstrap_probe, worker_packet(credential),
                environment_names=(), timeout_seconds=8, cancelled=lambda: False,
                account_credential=credential)
            self.assertEqual(os.environ[_SENTINEL], "ambient-marker")
        self.assertEqual(output, {"imported_primary": False, "imported_sentinel": False,
                                  "runtime_primary": False, "selected_credential": True})
        with self.assertRaises(OSError):
            os.fstat(fd)

    @unittest.skipUnless(sys.platform == "linux", "Linux subtree supervision")
    def test_bootstrap_write_failure_is_unknown_and_reaps_the_real_child(self):
        credential = _lease()
        fd = credential._fd
        tracker_fd = resource_tracker.getfd()
        original_open = builtins.open
        original_start = worker.start_process
        launched = []
        descendants = []
        bootstrap_fds = []
        passed_fds = []

        def interrupted(file, mode="r", *args, **kwargs):
            stream = original_open(file, mode, *args, **kwargs)
            if not launched or type(file) is not int or mode != "wb":
                return stream
            bootstrap_fds.append(file)

            class Writer:
                def __enter__(self):
                    return self

                def write(self, payload):
                    raise OSError("synthetic bootstrap transfer interruption")

                def __exit__(self, *_):
                    stream.close()

            return Writer()

        def capture_start(*args, **kwargs):
            process = original_start(*args, **kwargs)
            launched.append(process)
            passed_fds.extend(kwargs["pass_fds"])
            end = time.monotonic() + 2
            children_file = Path(f"/proc/{process.pid}/task/{process.pid}/children")
            while time.monotonic() < end:
                descendants[:] = [int(pid) for pid in children_file.read_text().split()]
                if descendants:
                    break
                time.sleep(0.01)
            self.assertTrue(descendants, "supervisor must have launched the spawn bootstrap")
            for pid in [process.pid, *descendants]:
                self.assertEqual(os.getpgid(pid), os.getpgrp())
            return process

        try:
            with patch.object(worker, "start_process", side_effect=capture_start), \
                    patch.object(builtins, "open", side_effect=interrupted), \
                    patch.object(worker, "stop_process", wraps=worker.stop_process) as cleanup:
                with self.assertRaises(worker.WorkerFailure) as failed:
                    worker.execute_worker(account_bootstrap_probe, worker_packet(credential),
                        environment_names=(), timeout_seconds=8, cancelled=lambda: False,
                        account_credential=credential)
            self.assertEqual(failed.exception.code, "worker_protocol_failed")
            self.assertTrue(failed.exception.may_have_executed)
            self.assertTrue(failed.exception.observation_lost)
            self.assertIsInstance(failed.exception.__cause__, OSError)
            self.assertEqual(str(failed.exception.__cause__), "synthetic bootstrap transfer interruption")
            self.assertEqual(len(launched), 1)
            cleanup.assert_called_once_with(launched[0])
            self.assertTrue(launched[0].cleanup_complete)
            for pid in [launched[0].pid, *descendants]:
                self.assertFalse(Path(f"/proc/{pid}").exists(), f"bootstrap process {pid} leaked")
            for closed_fd in {fd, *bootstrap_fds, *passed_fds} - {tracker_fd}:
                with self.assertRaises(OSError):
                    os.fstat(closed_fd)
        finally:
            credential.close()
            for process in launched:
                worker.stop_process(process)


if __name__ == "__main__":
    unittest.main()
