"""Offline metadata diagnostics and caller recovery through original bootstrap.

Synthetic metadata, byte responses and wheel fixtures never contact GitHub.
The recovery case runs the existing reduced local installation fixture.
"""
from http.client import HTTPException
from io import BytesIO
import json
import traceback
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from cpn.rpnh import install_v2, published_install as bootstrap
from cpn.rpnh import published_releases as releases
from tests.test_published_bootstrap import BootstrapTestCase, SUPPORT_NAME, VALIDATION_NAME


SECRET = "https://release-assets.githubusercontent.com/a?token=fixture-secret Authorization: Bearer fixture-header"


class PublishedMetadataDiagnosticTests(BootstrapTestCase):
    def subjects(self):
        return [(releases.RELEASES_URL, "release index"),
                (self.fixture.release_directory + SUPPORT_NAME, "official sidecar " + SUPPORT_NAME),
                (self.fixture.release_directory + VALIDATION_NAME, "official sidecar " + VALIDATION_NAME)]

    def assert_safe_failure(self, operation, subject, category):
        try:
            operation()
        except bootstrap.PublishedBootstrapError as exc:
            self.assertEqual(str(exc),
                f"official bootstrap metadata read failed: {subject} ({category})")
            rendered = "".join(traceback.format_exception(exc))
            for unsafe in (SECRET, "https://", "fixture-secret", "Authorization", "fixture-header"):
                self.assertNotIn(unsafe, rendered)
            self.assertTrue(exc.__suppress_context__)
        else:
            self.fail("expected a metadata-read failure")

    def test_transport_categories_identify_each_read_without_secrets_or_retry(self):
        errors = [(HTTPError(SECRET, 403, SECRET, {"Location": SECRET}, None), "HTTP 403"),
                  (TimeoutError(SECRET), "timeout"),
                  (URLError(TimeoutError(SECRET)), "timeout"),
                  (ConnectionResetError(SECRET), "connection failure"),
                  (URLError(OSError(SECRET)), "connection failure"),
                  (URLError(SECRET), "connection failure"),
                  (HTTPException(SECRET), "connection failure")]
        for index, (target, subject) in enumerate(self.subjects()):
            for error, category in errors:
                calls = []
                def read(url):
                    calls.append(url)
                    if url == target:
                        raise error
                    return self.fixture.get_json(url)
                with self.subTest(subject=subject, category=category, error=type(error).__name__), \
                        self.transports(get_json=read):
                    self.assert_safe_failure(self.fixture.inspect, subject, category)
                self.assertEqual(calls, [url for url, _ in self.subjects()][:index + 1])
                self.assertFalse(self.root.exists())
                self.assertEqual(self.fixture.wheel_calls, [])

    def test_original_bounded_json_reader_reports_invalid_and_oversized_bytes(self):
        cases = [(b"{\"url\":\"" + SECRET.encode() + b"\",", "invalid metadata"),
                 (b"x" * (releases.MAX_JSON_BYTES + 1), "metadata exceeds size limit")]
        for index, (target, subject) in enumerate(self.subjects()):
            for payload, category in cases:
                requests, reads = [], []
                class Response(BytesIO):
                    def read(self, amount):
                        reads.append(amount)
                        return super().read(amount)
                def open_response(request, *, timeout):
                    requests.append((request.full_url, timeout))
                    data = payload if request.full_url == target else json.dumps(
                        self.fixture.data[request.full_url]).encode()
                    return Response(data)
                opener = mock.Mock()
                opener.open.side_effect = open_response
                with self.subTest(subject=subject, category=category), \
                        mock.patch.object(releases, "build_opener", return_value=opener):
                    self.assert_safe_failure(self.fixture.inspect, subject, category)
                self.assertEqual(requests, [(url, releases.FETCH_TIMEOUT)
                    for url, _ in self.subjects()][:index + 1])
                self.assertEqual(reads, [releases.MAX_JSON_BYTES + 1] * (index + 1))
                self.assertEqual(self.fixture.wheel_calls, [])
                self.assertFalse(self.root.exists())

    def test_original_redirect_rejection_has_safe_read_subject(self):
        for index, (target, subject) in enumerate(self.subjects()):
            calls = []
            def build_opener(handler):
                def open_response(request, *, timeout):
                    calls.append(request.full_url)
                    if request.full_url == target:
                        handler.redirect_request(request, None, 302, SECRET, {},
                                                 "https://example.invalid/?token=fixture-secret")
                        self.fail("original redirect guard must reject")
                    return BytesIO(json.dumps(self.fixture.data[request.full_url]).encode())
                return mock.Mock(open=open_response)
            with self.subTest(subject=subject), \
                    mock.patch.object(releases, "build_opener", side_effect=build_opener):
                self.assert_safe_failure(self.fixture.inspect, subject, "metadata redirect rejected")
            self.assertEqual(calls, [url for url, _ in self.subjects()][:index + 1])
            self.assertFalse(self.root.exists())

    def test_structural_errors_after_read_keep_original_diagnostic(self):
        generic = "official bootstrap metadata or asset binding is unavailable or invalid"
        for stage in ("release", "sidecar", "material"):
            fixture = self.new_fixture()
            if stage == "release":
                fixture.release["assets"][0]["browser_download_url"] = SECRET
            elif stage == "sidecar":
                del fixture.support["schema_version"]
            else:
                fixture.release["assets"][1]["size"] += 1
            with self.subTest(stage=stage), self.transports(fixture), \
                    self.assertRaises(bootstrap.PublishedBootstrapError) as caught:
                fixture.inspect()
            expected = ("selected wheel differs from the official release asset identity"
                        if stage == "material" else generic)
            self.assertEqual(str(caught.exception), expected)
            self.assertEqual(fixture.wheel_calls, [])
            self.assertFalse(self.root.exists())

    def test_scheduling_budget_checks_still_bound_the_original_single_read(self):
        for clock, expected_calls in (([0, releases.SCHEDULE_BUDGET + 1], 0),
                                      ([0, 0, releases.SCHEDULE_BUDGET + 1], 1)):
            with self.subTest(clock=clock), self.transports(), \
                    mock.patch.object(bootstrap.time, "monotonic", side_effect=clock), \
                    self.assertRaisesRegex(bootstrap.PublishedBootstrapError,
                                           "^official metadata exceeded its scheduling budget$"):
                self.fixture.inspect()
            self.assertEqual(len(self.fixture.json_calls), expected_calls)
            self.fixture.json_calls.clear()
            self.assertFalse(self.root.exists())

    def test_caller_retry_recovers_through_actual_acquisition_rereads_and_lock(self):
        import fcntl
        fixture = self.new_fixture(runnable=True)
        with self.transports(fixture):
            request = fixture.inspect()
        failed_calls = []
        def failed_read(url):
            failed_calls.append(url)
            raise URLError(TimeoutError(SECRET))
        with self.transports(fixture, get_json=failed_read):
            self.assert_safe_failure(lambda: bootstrap.bootstrap_published(self.root, request),
                                     "release index", "timeout")
        self.assertEqual(failed_calls, [releases.RELEASES_URL])
        self.assertEqual(fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())
        held_lock = []
        def recovered_read(url):
            if url == releases.RELEASES_URL and fixture.json_calls.count(url) == 3:
                with (self.root / "lifecycle.lock").open("rb") as stream:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(stream.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                self.assertEqual(list((self.root / "versions").iterdir()), [])
                held_lock.append(True)
            return fixture.get_json(url)
        with self.transports(fixture, get_json=recovered_read):
            result = bootstrap.bootstrap_published(self.root, request)
        target, directory, installed = install_v2.read_target(self.root)
        self.assertTrue(result.activation.committed)
        self.assertEqual(result.activation.current, target)
        self.assertEqual(installed["source"], fixture.source)
        self.assertTrue((directory / "venv/bin/python").is_file())
        self.assertEqual(held_lock, [True])
        self.assertEqual(fixture.json_calls, [releases.RELEASES_URL,
            fixture.release_directory + SUPPORT_NAME,
            fixture.release_directory + VALIDATION_NAME] * 4)
        self.assertEqual([row["id"] for row in fixture.wheel_calls],
            [row["asset"]["id"] for row in request.to_document()["materials"]["python_wheels"]])


if __name__ == "__main__":
    unittest.main()
