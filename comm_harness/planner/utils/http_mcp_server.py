"""In-process streamable-HTTP MCP server that wraps a robot ``Toolkit``.

Modeled after RPent's ``rpent/planner/utils/http_mcp_server.py``.
Uses Python's built-in ``ThreadingHTTPServer`` so it runs in both the Python 3.8
Habitat environment and Python 3.10+ without requiring external ASGI/MCP packages,
while speaking the standard JSON-RPC 2.0 MCP protocol (`initialize`,
`notifications/initialized`, `tools/list`, `tools/call`) expected by Codex.
"""

from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import socket
import threading
from typing import Any, Dict, List, Optional

from comm_harness.planner.base import (
    SERVER_NAME,
    invoke_toolkit_sync,
    strip_mcp_prefix,
)

logger = logging.getLogger("http_mcp_server")


def mcp_result(raw_result: Any) -> Dict[str, Any]:
    """Convert a toolkit outcome into an MCP ``CallToolResult`` dictionary."""
    if hasattr(raw_result, "to_text") and hasattr(raw_result, "data"):
        content: List[Dict[str, Any]] = [
            {"type": "text", "text": raw_result.to_text()},
        ]
        for img in getattr(raw_result, "images", ()) or ():
            b64 = (
                base64.b64encode(img).decode("ascii")
                if isinstance(img, (bytes, bytearray))
                else str(img)
            )
            content.append({"type": "image", "data": b64, "mimeType": "image/png"})
        return {"content": content, "isError": bool(getattr(raw_result, "is_error", False))}

    if isinstance(raw_result, dict):
        is_err = raw_result.get("ok") is False or bool(raw_result.get("error"))
        return {
            "content": [{"type": "text", "text": json.dumps(raw_result, ensure_ascii=False)}],
            "isError": is_err,
        }

    return {
        "content": [{"type": "text", "text": str(raw_result)}],
        "isError": False,
    }


def list_mcp_tools(toolkit: Any) -> List[Dict[str, Any]]:
    """Extract MCP tool descriptors (`name`, `description`, `inputSchema`) from a toolkit."""
    tools: List[Dict[str, Any]] = []
    if hasattr(toolkit, "list_tools") and callable(toolkit.list_tools):
        for decl in toolkit.list_tools():
            name = getattr(decl, "name", None) or decl.get("name")
            desc = getattr(decl, "description", "") or decl.get("description", "")
            schema = (
                getattr(decl, "input_schema", None)
                or getattr(decl, "parameters", None)
                or (decl.get("input_schema") if isinstance(decl, dict) else None)
                or {"type": "object", "properties": {}}
            )
            tools.append({"name": str(name), "description": str(desc), "inputSchema": schema})
    elif hasattr(toolkit, "schemas") and callable(toolkit.schemas):
        for item in toolkit.schemas():
            fn = item.get("function", item)
            tools.append(
                {
                    "name": str(fn.get("name", "")),
                    "description": str(fn.get("description", "")),
                    "inputSchema": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
    return tools


def handle_jsonrpc_message(toolkit: Any, body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Process one MCP JSON-RPC 2.0 request or notification."""
    method = body.get("method", "")
    req_id = body.get("id")
    params = body.get("params") or {}

    # Notifications have no id and expect no JSON-RPC response object
    if req_id is None and method.startswith("notifications/"):
        return None

    if method == "initialize":
        protocol_version = params.get("protocolVersion", "2024-11-05")
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "0.1.0"},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": list_mcp_tools(toolkit)},
        }

    if method == "tools/call":
        raw_name = str(params.get("name", ""))
        lookup = strip_mcp_prefix(raw_name)
        arguments = params.get("arguments") or {}
        try:
            outcome = invoke_toolkit_sync(toolkit, lookup, arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": mcp_result(outcome),
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def _pick_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


class HttpMcpServer:
    """Run an in-process streamable-HTTP MCP server over a ``Toolkit`` on a daemon thread."""

    def __init__(
        self,
        toolkit: Any,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        path: str = "/mcp",
    ) -> None:
        self._toolkit = toolkit
        self._host = host
        self._port = port or _pick_free_port(host)
        self._path = path if path.startswith("/") else f"/{path}"
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}{self._path}/"

    def start(self, *, ready_timeout_s: float = 10.0) -> str:
        """Start the background HTTP MCP server and return its endpoint URL."""
        if self._thread is not None:
            return self.url

        toolkit = self._toolkit

        class _McpHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return  # Suppress noisy per-request stderr logging

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except Exception as exc:
                    err = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {exc}"},
                    }
                    self._send_json(400, err)
                    return

                response = handle_jsonrpc_message(toolkit, payload)
                if response is None:
                    self.send_response(202)
                    self.end_headers()
                    return
                self._send_json(200, response)

            def _send_json(self, status: int, data: Dict[str, Any]) -> None:
                encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self._httpd = ThreadingHTTPServer((self._host, self._port), _McpHandler)
        self._port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="mcp-http-server",
            daemon=True,
        )
        self._thread.start()
        logger.info("HttpMcpServer ready at %s", self.url)
        return self.url

    def stop(self, *, timeout_s: float = 5.0) -> None:
        """Shut down the background HTTP MCP server and release the port."""
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
        self._httpd = None
        self._thread = None

    def __enter__(self) -> "HttpMcpServer":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop()
