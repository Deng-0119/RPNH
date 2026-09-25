"""Offline stdio MCP test peer (2025-11-25 subset), not a production server.

Only initialize, notifications/initialized and one tools/call are implemented.
The real subprocess/JSON-RPC exchange tests host admission of an adapter; it
makes no claim about production MCP transport/authentication compatibility.
"""
import json
import os
import sys

initialized = False
ready = False
for line in sys.stdin:
    message = json.loads(line)
    assert message["jsonrpc"] == "2.0"
    method = message["method"]
    if method == "initialize":
        assert not initialized
        initialized = True
        response = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "offline-test-peer", "version": "1"}}
    elif method == "notifications/initialized":
        assert initialized and not ready and "id" not in message
        ready = True
        continue
    else:
        assert method == "tools/call" and ready
        assert message["params"]["name"] == "double"
        value = 2 * message["params"]["arguments"]["value"]
        response = {"content": [{"type": "text", "text": str(value)}],
                    "structuredContent": {"value": value, "server_pid": os.getpid()},
                    "isError": False}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": response}), flush=True)
    if method == "tools/call":
        break
