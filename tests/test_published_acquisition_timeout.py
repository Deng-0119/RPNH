"""Offline transport fixtures; no public download or installer execution.

The slow response simulates elapsed time and a socket timeout at the opener
seam. It is a deterministic regression, not evidence of CDN/network success.
"""
from io import BytesIO
from pathlib import Path
import tempfile
import traceback
import unittest
from unittest import mock
from zipfile import ZIP_STORED, ZipFile

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

    def test_body_budget_failure_has_fixed_category_without_transport_secrets(self):
        self.check_failure(download.PublishedTransferTimeout, "transfer time budget exceeded")


class PublishedWheelBodyBudgetTests(BootstrapTestCase):
    def test_large_wheel_finishes_after_thirty_seconds_with_original_material_checks(self):
        fixture = self.fixture
        size = 5_233_120
        original = fixture.payloads[701]
        # One stored fixture member makes a real readable wheel of the observed
        # release size, without network I/O or an installed environment.
        def padded(amount):
            output = BytesIO(original)
            with ZipFile(output, "a") as archive:
                archive.writestr("fixture-padding.bin", b"x" * amount, compress_type=ZIP_STORED)
            return output.getvalue()
        fixture.payloads[701] = padded(size - len(padded(0)))
        self.assertEqual(len(fixture.payloads[701]), size)
        fixture.release["assets"][0]["size"] = size
        for declaration in (fixture.support["combinations"][0], fixture.validation["results"][0]):
            root = next(row for row in declaration["materials"]["python_wheels"]
                        if row["name"] == "rpnh-harness")
            root["asset"]["size"] = size
        with self.transports():
            document = fixture.inspect().to_document()
        clock = [0.0]
        budgets = []
        actual_stream = download._stream
        class SlowResponse(WheelResponse):
            def read1(self, amount):
                clock[0] += 2.5
                return self.read(amount)
        def open_response(asset):
            fixture.wheel_calls.append(asset)
            return SlowResponse(fixture.payloads[asset["id"]])
        def stream(response, output, expected_size, **options):
            budgets.append((expected_size, options["seconds"]))
            return actual_stream(response, output, expected_size, **options)
        with mock.patch.object(bootstrap, "_open_wheel", side_effect=open_response), \
                mock.patch.object(bootstrap, "_stream", side_effect=stream), \
                mock.patch.object(download.time, "monotonic", side_effect=lambda: clock[0]):
            with bootstrap._acquire_plan_materials(self.root, document) as (_, _, materials, files):
                self.assertEqual(materials.document["published_selection"], document["materials"])
                root = next(row for row in materials.document["python_wheels"]
                            if row["name"] == "rpnh-harness")
                self.assertEqual(Path(root["path"]).stat().st_size, size)
                self.assertEqual(len(files), 2)
        self.assertGreater(clock[0], 30)
        self.assertLess(clock[0], 300)
        self.assertEqual(dict(budgets)[size], 300)
        self.assertEqual(dict(budgets)[len(fixture.payloads[702])], 30)
        self.assertEqual([row["id"] for row in fixture.wheel_calls],
            [row["asset"]["id"] for row in document["materials"]["python_wheels"]])
        self.assertFalse(self.root.exists())

    def test_small_default_and_maximum_body_budgets_remain_enforced(self):
        self.assertEqual(bootstrap._wheel_stream_seconds(2 * 1024 * 1024), 128)
        self.assertEqual(bootstrap._wheel_stream_seconds(download.MAX_WHEEL_BYTES), 300)
        # Both checks reject: before reading, and after a read crosses the limit.
        for options, seconds in (({}, 30),
                ({"seconds": bootstrap._wheel_stream_seconds(download.MAX_WHEEL_BYTES)}, 300)):
            for moments in ([0, seconds + 1], [0, 0, seconds + 1]):
                with self.subTest(seconds=seconds, moments=moments), \
                        tempfile.TemporaryFile() as output, \
                        mock.patch.object(download.time, "monotonic", side_effect=moments), \
                        self.assertRaisesRegex(download.PublishedTransferTimeout,
                                               "^wheel transfer exceeded its time budget$"):
                    download._stream(WheelResponse(b"abc"), output, 3, **options)
                self.assertFalse(self.root.exists())

    def test_longer_body_budget_keeps_size_and_encoding_rejections(self):
        for kind in ("header-size", "encoded", "overrun", "truncated"):
            response = WheelResponse(b"abc")
            size = 3
            if kind == "header-size":
                response.headers["Content-Length"] = "4"
            elif kind == "encoded":
                response.headers["Content-Encoding"] = "gzip"
            else:
                response.headers.clear()
                size = 2 if kind == "overrun" else 4
            with self.subTest(kind=kind), tempfile.TemporaryFile() as output, \
                    self.assertRaises(download.PublishedDownloadError) as caught:
                download._stream(response, output, size, seconds=300)
            self.assertNotIsInstance(caught.exception, download.PublishedTransferTimeout)
