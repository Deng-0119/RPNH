"""Real Python server stdio history smoke with the actual Node page decoder.

Only the server frame limit is reduced to 512 bytes to force a small empty
history through the negotiated transport. No Registry authority, backend,
owner, provider or TypeScript package is replaced.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PROTOCOL = "rpnh/dsh/v1"


class DshServerTransportOfflineTests(unittest.TestCase):
    def test_actual_server_fragments_negotiate_and_node_reassembles_exact_history(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="rpnh-server-transport-") as temporary:
            root = Path(temporary)
            session_id = "transport-" + "x" * 90
            commands = [
                {"id": "original-command", "method": "history", "accept_reply_fragments": True},
                {"id": "legacy-command", "method": "history", "accept_reply_fragments": False},
                {"id": "invalid-flag", "method": "turn", "request": {}, "accept_reply_fragments": "yes"},
                {"id": "unchanged-history", "method": "history", "accept_reply_fragments": True},
                {"id": "close-command", "method": "close"},
            ]
            request = b"".join(json.dumps({"protocol": PROTOCOL, "kind": "command", **command},
                separators=(",", ":")).encode() + b"\n" for command in commands)
            environment = dict(os.environ)
            environment.pop("RPNH_PLUGIN_CONFIG", None)
            run = subprocess.run([
                sys.executable, "-u", "-c",
                "import cpn.dsh.server as server; server.MAX_BYTES=512; server.main()",
                "--root", str(root), "--session", session_id, "--offline", "--create",
            ], cwd=source, env=environment, input=request, capture_output=True, timeout=30)
            lines = run.stdout.splitlines()
            frames = [json.loads(line) for line in lines]
            self.assertEqual(run.returncode, 0, run.stderr.decode())
            self.assertEqual(run.stderr, b"")
            self.assertTrue(all(len(line) <= 512 for line in lines))
            self.assertEqual(frames[0], {"protocol": PROTOCOL, "kind": "ready", "session_id": session_id})
            groups = {command["id"]: [frame for frame in frames if frame.get("id") == command["id"]]
                      for command in commands}

            def reassemble(command_id):
                pages = groups[command_id]
                self.assertGreater(len(pages), 1)
                offset, parts = 0, []
                total = pages[0]["fragment"]["total_chars"]
                for frame in pages:
                    self.assertEqual(frame["kind"], "reply-fragment")
                    self.assertEqual(frame["protocol"], PROTOCOL)
                    page = frame["fragment"]
                    self.assertEqual(page["offset_chars"], offset)
                    self.assertEqual(page["total_chars"], total)
                    self.assertTrue(page["content"])
                    parts.append(page["content"])
                    offset += len(page["content"])
                    self.assertEqual(page["next_offset_chars"], offset if offset < total else None)
                self.assertEqual(offset, total)
                return json.loads("".join(parts))

            original = reassemble("original-command")
            unchanged = reassemble("unchanged-history")
            self.assertEqual(unchanged, original)
            self.assertEqual(original["committed_history"], [])
            self.assertIsNone(original["active"])
            self.assertEqual(original["session_id"], session_id)
            self.assertEqual(len(groups["legacy-command"]), 1)
            self.assertEqual(groups["legacy-command"][0]["error"], {
                "type": "ValueError", "message": "outbound frame exceeds size limit"})
            self.assertEqual(len(groups["invalid-flag"]), 1)
            self.assertEqual(groups["invalid-flag"][0]["error"], {
                "type": "ValueError", "message": "reply fragment negotiation must be boolean"})
            self.assertEqual(groups["close-command"][0]["result"], {"closed": True})

            actual_pages = b"\n".join(line for line, frame in zip(lines, frames, strict=True)
                                       if frame.get("id") == "original-command") + b"\n"
            program = """
import { readFileSync } from 'node:fs';
import { ReplyFragments } from './integrations/dsh/src/reply-codec.ts';
const input = readFileSync(0, 'utf8').trim().split('\\n');
const decoder = new ReplyFragments(); let final;
for (let index = 0; index < input.length; index++) {
  const frame = JSON.parse(input[index]);
  const result = decoder.append(frame.fragment);
  if (index < input.length - 1 && result.complete) throw new Error('partial server reply resolved');
  final = result;
}
if (!final.complete) throw new Error('actual server reply never completed');
process.stdout.write(JSON.stringify(final.value));
"""
            node = subprocess.run(["node", "--input-type=module", "-e", program],
                cwd=source, input=actual_pages, capture_output=True, timeout=15, check=True)
            self.assertEqual(json.loads(node.stdout), original)
            self.assertEqual(node.stderr, b"")
            target = os.environ.get("RPNH_SERVER_TRANSPORT_EVIDENCE_DIR")
            if target:
                destination = Path(target) / self._testMethodName
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "requests.jsonl").write_bytes(request)
                (destination / "server-frames.jsonl").write_bytes(run.stdout)
                (destination / "node-decoded-history.json").write_bytes(node.stdout + b"\n")
                (destination / "facts.json").write_text(json.dumps({
                    "method": self.id(), "frame_limit_bytes": 512,
                    "frame_sizes_bytes": [len(line) for line in lines],
                    "fragment_counts": {name: len(groups[name]) for name in (
                        "original-command", "unchanged-history")},
                    "server_exit_code": run.returncode, "node_exit_code": node.returncode,
                    "legacy_oversize_rejected": True, "invalid_negotiation_rejected": True,
                    "original_history_unchanged": True,
                    "scope": "Actual Python server private stdio and Registry parent history plus actual detached Node ReplyFragments",
                    "substitution": "Server frame byte limit only: 512 instead of production MAX_BYTES",
                    "not_exercised": ["Native DSH OwnerClient dependency package", "AF_UNIX OwnerEventLoop", "Provider/API", "Business turn execution"],
                }, indent=2) + "\n")
                shutil.copytree(root, destination / "registered-session", dirs_exist_ok=True)


if __name__ == "__main__":
    unittest.main()

