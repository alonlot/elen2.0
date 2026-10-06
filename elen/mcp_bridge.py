"""MCP stdio server that gives `claude -p` the Elen tools.

`claude -p` starts this process (see the MCP config Elen writes). Each MCP
request is forwarded to the Elen daemon over a private Unix socket. The daemon
runs the tool through its guard, so approvals and rule checks still apply.

Standard library only, so it starts fast.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from typing import Any

PROTOCOL_VERSION = "2025-06-18"


def ask_daemon(sock_path: str, request: dict[str, Any]) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(sock_path)
        s.sendall(json.dumps(request).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf or b"{}")


def handle(sock_path: str, msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    mid = msg.get("id")
    if mid is None:  # a notification, no answer
        return None
    try:
        if method == "initialize":
            client_version = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION
            result: Any = {
                "protocolVersion": client_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "elen", "version": "2.0"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            tools = ask_daemon(sock_path, {"op": "list"}).get("tools", [])
            result = {
                "tools": [
                    {"name": t["name"], "description": t["description"], "inputSchema": t["parameters"]}
                    for t in tools
                ]
            }
        elif method == "tools/call":
            params = msg.get("params") or {}
            reply = ask_daemon(
                sock_path,
                {"op": "call", "name": params.get("name", ""), "arguments": params.get("arguments") or {}},
            )
            result = {
                "content": [{"type": "text", "text": reply.get("content", "")}],
                "isError": bool(reply.get("is_error")),
            }
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}}
    except OSError as e:
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": -32000, "message": f"Elen daemon is not reachable: {e}"},
        }
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        reply = handle(args.socket, msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
