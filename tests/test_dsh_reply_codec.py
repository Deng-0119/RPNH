"""Offline private transport checks; no provider or Registry mutation."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from cpn.dsh.reply_codec import encode_reply_frames


PROTOCOL = 'rpnh/dsh/v1'


class DshReplyCodecTests(unittest.TestCase):
    def frames(self, value, *, limit=512, fragments=True):
        return list(encode_reply_frames('original-command', value, protocol=PROTOCOL,
                                       max_bytes=limit, accept_fragments=fragments))

    def test_small_reply_retains_the_original_envelope(self):
        result = {'status': 'idle', 'answer': None}
        self.assertEqual(self.frames(result), self.frames(result, fragments=False))
        self.assertEqual(json.loads(self.frames(result)[0]), {
            'protocol': PROTOCOL, 'kind': 'reply', 'id': 'original-command', 'result': result})

    def test_complete_frames_and_exact_reassembly_for_all_reply_shapes(self):
        calls = [{'id': f'call-{index}', 'text': '\x00\n\\\"中文😀' * 30}
                 for index in range(30)]
        answer = {'status': 'completed', 'calls': calls}
        shapes = [
            {'status': 'committed', 'answer': answer},
            {'status': 'replayed', 'answer': answer},
            {'status': 'terminal', 'answer': answer, 'history': {'active': None}},
            {'committed_history': [{'ordinal': 1, 'answer': answer}], 'active': None},
        ]
        for value in shapes:
            with self.subTest(shape=next(iter(value))):
                frames = self.frames(value)
                self.assertGreater(len(frames), 1)
                offset, parts = 0, []
                for raw in frames:
                    self.assertLessEqual(len(raw), 512)
                    frame = json.loads(raw)
                    self.assertEqual(frame['id'], 'original-command')
                    self.assertEqual(frame['kind'], 'reply-fragment')
                    page = frame['fragment']
                    self.assertEqual(page['offset_chars'], offset)
                    self.assertTrue(page['content'])
                    parts.append(page['content']); offset += len(page['content'])
                    self.assertEqual(page['next_offset_chars'],
                                     offset if offset < page['total_chars'] else None)
                self.assertEqual(json.loads(''.join(parts)), value)

    def test_old_client_overflow_is_explicit_and_emits_no_fragment(self):
        output = encode_reply_frames('original-command', {'text': 'x' * 2000},
                                    protocol=PROTOCOL, max_bytes=512, accept_fragments=False)
        with self.assertRaisesRegex(ValueError, 'size limit'):
            next(output)

    def test_invalid_data_and_too_small_budget_fail_before_emission(self):
        for value, limit, fragments in [({'x': float('nan')}, 512, True),
                                       ({'x': 'x' * 1000}, 128, True),
                                       ({'x': 'x'}, 512, 'yes')]:
            with self.subTest(limit=limit, fragments=fragments):
                with self.assertRaises(ValueError):
                    next(encode_reply_frames('original-command', value, protocol=PROTOCOL,
                                             max_bytes=limit, accept_fragments=fragments))

    def test_exact_threshold_keeps_small_replies_and_fragments_one_byte_above(self):
        value = {'text': 'x' * 1000}
        exact = len(self.frames(value, limit=2000)[0])
        self.assertEqual(len(self.frames(value, limit=exact)), 1)
        frames = self.frames(value, limit=exact - 1)
        self.assertGreater(len(frames), 1)
        self.assertTrue(all(len(frame) < exact for frame in frames))

    def test_actual_node_decoder_consumes_python_frames(self):
        root = Path(__file__).resolve().parents[1]
        value = {'status': 'replayed', 'answer': {'calls': [
            {'id': str(index), 'page': '\x00\n\\\"中文😀' * 30} for index in range(40)]}}
        frames = self.frames(value)
        program = """
import { readFileSync } from 'node:fs';
import { ReplyFragments } from './integrations/dsh/src/reply-codec.ts';
const decoder = new ReplyFragments(); let result;
for (const line of readFileSync(0, 'utf8').trim().split('\\n')) {
  result = decoder.append(JSON.parse(line).fragment);
}
if (!result.complete) throw new Error('incomplete actual Python stream');
process.stdout.write(JSON.stringify(result.value));
"""
        run = subprocess.run(['node', '--input-type=module', '-e', program], cwd=root,
                             input=b'\n'.join(frames) + b'\n', capture_output=True, check=True)
        self.assertEqual(json.loads(run.stdout), value)


if __name__ == '__main__':
    unittest.main()

