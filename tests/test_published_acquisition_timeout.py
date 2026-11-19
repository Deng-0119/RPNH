"""Offline transport fixtures; no public download or installer execution.

The slow response simulates elapsed time and a socket timeout at the opener
seam. It is a deterministic regression, not evidence of CDN/network success.
"""
import tempfile
import traceback
import unittest
from unittest import mock

from cpn.rpnh import published_download as download
from cpn.rpnh import published_install as bootstrap
from tests.test_published_bootstrap import BootstrapTestCase, WheelResponse


class PublishedSocketTimeoutTests(unittest.TestCase):
    def test_valid_stream_with_read_over_two_seconds_uses_fifteen_second_timeout(self):
        payload = b"x" * 32768 + b"tail"
        clock = [0.0]
        timeouts = []

        class SlowResponse(WheelResponse):
            def read1(self, amount):
                if self.tell() == 32768:
                    clock[0] += 2.1
                    if 2.1 > timeouts[-1]:
                        raise TimeoutError("fixture socket read exceeded configured timeout")
                return self.read(min(amount, 32768))

        def open_response(request, *, timeout):
            timeouts.append(timeout)
            return SlowResponse(payload)

        opener = mock.Mock()
        opener.open.side_effect = open_response
        with mock.patch.object(download, "build_opener", return_value=opener), \
                mock.patch.object(download.time, "monotonic", side_effect=lambda: clock[0]), \
                tempfile.TemporaryFile() as output:
            with download._open_wheel({"id": 763998856}) as response:
                self.assertEqual(download._stream(response, output, len(payload)), len(payload))
            self.assertEqual(output.tell(), len(payload))
        self.assertEqual(timeouts, [15.0])
        self.assertGreater(clock[0], 2.0)
        self.assertLess(clock[0], download.STREAM_SECONDS)
        opener.open.assert_called_once()


class PublishedAcquisitionDiagnosticTests(BootstrapTestCase):
    def check_failure(self, error_type, category):
        # Exercise connection/header and mid-stream failures with synthetic
        # secrets that must never reach the user-facing diagnostic/traceback.
        secret = "https://release-assets.githubusercontent.com/a?token=fixture-secret Authorization: Bearer fixture-header"
        with self.transports():
            document = self.fixture.inspect().to_document()
        selected = document["materials"]["python_wheels"][0]
        prefix = "exact official wheel acquisition failed; no installation was activated"
        for stage in ("open", "read"):
            error = error_type(secret)
            response = WheelResponse(self.fixture.payloads[selected["asset"]["id"]])
            if stage == "read":
                response.read1 = mock.Mock(side_effect=error)
            with self.subTest(stage=stage), \
                    mock.patch.object(bootstrap, "_open_wheel", side_effect=error if stage == "open" else None,
                                      return_value=response) as transport:
                try:
                    with bootstrap._acquire_plan_materials(self.root, document):
                        self.fail("failed transport must not yield installable materials")
                except bootstrap.PublishedBootstrapError as exc:
                    self.assertEqual(str(exc), "{}: {} ({})".format(prefix, selected["filename"], category))
                    rendered = "".join(traceback.format_exception(exc))
                    for unsafe in (secret, "https://", "fixture-secret", "Authorization", "fixture-header"):
                        self.assertNotIn(unsafe, rendered)
                    self.assertTrue(exc.__suppress_context__)
                else:
                    self.fail("expected acquisition failure")
            transport.assert_called_once()
            self.assertFalse(self.root.exists())

    def test_timeout_identifies_selected_filename_without_transport_secrets(self):
        self.check_failure(TimeoutError, "timeout")

    def test_generic_failure_identifies_selected_filename_without_transport_secrets(self):
        self.check_failure(OSError, "unavailable or invalid")
