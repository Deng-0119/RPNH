"""Pure in-memory framing checks: no socket, provider or Registry execution."""
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cpn.rpnh.collaboration.project_framing import (
    MAX_RESPONSE_BYTES, ResponseFrameError, encode_response, read_response,
)
from cpn.rpnh.collaboration.share_packages import DEFAULT_LIMITS
from cpn.rpnh.collaboration.subnet_exchange import SubnetExportResult
from cpn.rpnh.collaboration.project_entry import run_console
from cpn.rpnh.collaboration.project_local import _receive_commands


class FramingTests(unittest.TestCase):
    def test_large_legal_frames(self):
        # Sequential allocation: a >32 MiB frame also covers the former 8 MiB
        # cap, but keep both regression thresholds explicit.
        for size in (8 * 1024 * 1024 + 4, 32 * 1024 * 1024 + 4):
            with self.subTest(size=size):
                export = SubnetExportResult(SimpleNamespace(manifest_digest='a' * 64,
                                            archive_digest='b' * 64), (), 'c' * 64)
                # Download-shaped Base64 payload within the archive byte limit;
                # no archive import/export or business execution is performed.
                template = encode_response({'ok': True, 'result': {
                    'result': export.to_dict(), 'archive_base64': 'PAYLOAD',
                    'dependency_archives_base64': []}})
                prefix, _, suffix = template.partition(b'PAYLOAD')
                stream = BytesIO()
                stream.write(prefix)
                chunk = b'A' * (1024 * 1024)
                for _ in range(size // len(chunk)):
                    stream.write(chunk)
                stream.write(chunk[:size % len(chunk)])
                stream.write(suffix)
                stream.seek(0)
                result = read_response(stream)
                self.assertEqual(len(result['result']['archive_base64']), size)
                self.assertEqual(stream.read(), b'')
                del stream, result

    def test_exact_limit_and_one_byte_over(self):
        frame = encode_response({'ok': True, 'result': 'test'})
        self.assertTrue(read_response(BytesIO(frame), max_bytes=len(frame))['ok'])
        with self.assertRaisesRegex(ResponseFrameError, 'too_large'):
            read_response(BytesIO(frame), max_bytes=len(frame) - 1)
        self.assertEqual(encode_response({'ok': True, 'result': 'test'}, max_bytes=len(frame)), frame)

    def test_bounded_read_and_first_frame_only(self):
        class CheckedStream(BytesIO):
            def readline(self, size=-1):
                self.last_size = size
                return super().readline(size)
        frame = encode_response({'ok': True, 'result': 1})
        stream = CheckedStream(frame + frame)
        read_response(stream, max_bytes=len(frame))
        self.assertEqual(stream.last_size, len(frame) + 1)
        self.assertEqual(stream.read(), frame)

    def test_invalid_frames_and_envelopes(self):
        for raw in (b'', b'{"ok":true,"result":1}', b'\n', b'not json\n', b'\xff\n',
                    b'[]\n', b'{"ok":1,"result":0}\n', b'{"ok":true}\n',
                    b'{"ok":false,"error":"x"}\n', b'{"ok":true,"result":NaN}\n',
                    b'{"ok":true,"result":1e999}\n', b'{"ok":true,"ok":false,"result":0}\n',
                    b'{"ok":true,"result":{"a":1,"a":2}}\n',
                    b'{"ok":true,"result":1,"extra":0}\n'):
            with self.subTest(raw=raw), self.assertRaises(ResponseFrameError):
                read_response(BytesIO(raw))

    def test_export_schema_metadata_allowance_and_padding(self):
        n, budget = DEFAULT_LIMITS.packages, DEFAULT_LIMITS.closure_bytes
        result = SubnetExportResult(SimpleNamespace(manifest_digest='a' * 64,
                                    archive_digest='b' * 64), (b'',) * (n - 1), 'c' * 64)
        empty = {'ok': True, 'result': {'result': result.to_dict(), 'archive_base64': '',
                                     'dependency_archives_base64': [''] * (n - 1)}}
        metadata_bytes = len(encode_response(empty))
        self.assertLess(metadata_bytes, 8192)
        # Maximum permitted total archive bytes and package count, without
        # constructing a 90 MiB wire or importing any archive.
        sizes = [1] * (n - 2) + [DEFAULT_LIMITS.archive_bytes,
                                 budget - DEFAULT_LIMITS.archive_bytes - (n - 2)]
        self.assertEqual(sum(sizes), budget)
        self.assertTrue(all(size <= DEFAULT_LIMITS.archive_bytes for size in sizes))
        self.assertLessEqual(metadata_bytes + sum(4 * ((size + 2) // 3) for size in sizes),
                             MAX_RESPONSE_BYTES)

    def test_encoding_errors_are_short_structured_responses(self):
        for value in ({'ok': True, 'result': 'x' * 2048},
                      {'ok': True, 'result': float('nan')}, {'ok': 1}):
            raw = encode_response(value, max_bytes=512)
            self.assertLessEqual(len(raw), 512)
            response = read_response(BytesIO(raw), max_bytes=512)
            self.assertFalse(response['ok'])
            self.assertIn('may already have committed', response['message'])
            self.assertIn('new command ID', response['message'])
        with self.assertRaises(ResponseFrameError):
            encode_response({'ok': True, 'result': 'x' * 2048}, max_bytes=1)

    def test_console_dispatch_once_on_oversized_result(self):
        calls = []
        endpoint = SimpleNamespace(dispatch=lambda request: calls.append(request) or 'x' * 2048)
        output = BytesIO()
        with patch('cpn.rpnh.collaboration.project_entry.encode_response',
                   side_effect=lambda value: encode_response(value, max_bytes=512)):
            run_console(endpoint, BytesIO(b'{"op":"status"}\n'), output)
        self.assertEqual(calls, [{'op': 'status'}])
        self.assertEqual(read_response(BytesIO(output.getvalue()))['error'], 'response_too_large')

    def test_driver_records_transport_failure_without_socket(self):
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / 'examples/project_collaboration/scenario/operate_project.py'
        spec = importlib.util.spec_from_file_location('framing_test_driver', path)
        driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)
        class Client:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def connect(self, path): pass
            def makefile(self, mode):
                return SimpleNamespace(write=lambda raw: None, flush=lambda: None,
                                       readline=lambda size: b'{"ok":true')
        records = []
        journey = driver.Journey.__new__(driver.Journey)
        journey.root = Path('/unused-test-project')
        journey.record = lambda name, value: records.append((name, value))
        with patch.object(driver.socket, 'socket', return_value=Client()), \
                self.assertRaises(driver.ProjectCommandError):
            journey.client('project', 'owner', {'op': 'status'})
        self.assertEqual(len(records), 2)
        self.assertEqual(records[-1][0], 'project-owner-response')
        self.assertEqual(records[-1][1]['error'], 'incomplete_response_frame')

    def test_server_dispatch_once_on_oversized_result(self):
        calls, output = [], BytesIO()
        class Stream:
            def readline(self, size):
                return BytesIO(b'{"op":"status"}\n').readline(size)
            def write(self, data):
                return output.write(data)
            def flush(self):
                pass
        class Client:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def settimeout(self, value): pass
            def makefile(self, mode): return Stream()
        stopped = SimpleNamespace(done=False)
        stopped.is_set = lambda: stopped.done
        def select(timeout):
            stopped.done = True
            return [(SimpleNamespace(fileobj=SimpleNamespace(accept=lambda: (Client(), None)),
                                     data='owner'), None)]
        def submit(route, request):
            calls.append((route, request))
            return SimpleNamespace(result=lambda: 'x' * 2048)
        with patch('cpn.rpnh.collaboration.project_local.encode_response',
                   side_effect=lambda value: encode_response(value, max_bytes=512)):
            _receive_commands(SimpleNamespace(select=select), submit, stopped)
        self.assertEqual(calls, [('owner', {'op': 'status'})])
        self.assertEqual(read_response(BytesIO(output.getvalue()))['error'], 'response_too_large')


if __name__ == '__main__':
    unittest.main()
