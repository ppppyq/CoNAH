"""Lightweight RPent-style HTTP JSON-RPC server and client (`POST /call`, `GET /healthz`).

Built purely on the Python standard library (`http.server` + `urllib.request`) so
services (`env_server`, `comm_server`, `perception_server`) and agent harnesses
can run in separate OS processes or virtual environments on the same machine
(`127.0.0.1`) or across LAN without extra dependencies.
"""
from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from typing import Any, Callable, Dict, Optional
import urllib.error
import urllib.request


class RpcError(RuntimeError):
    """Raised when an RPC endpoint rejects or fails a remote method call."""


def encode_rpc_value(obj: Any) -> Any:
    """Encode Python / optional NumPy / bytes values into pure JSON."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (bytes, bytearray)):
        return {"__bytes__": base64.b64encode(bytes(obj)).decode("ascii")}
    if isinstance(obj, dict):
        return {str(k): encode_rpc_value(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [encode_rpc_value(v) for v in obj]
    # Duck-type NumPy ndarray and scalar without importing numpy at module scope
    if hasattr(obj, "dtype") and hasattr(obj, "shape") and hasattr(obj, "tobytes"):
        return {
            "__ndarray__": base64.b64encode(obj.tobytes()).decode("ascii"),
            "dtype": str(obj.dtype),
            "shape": list(obj.shape),
        }
    if hasattr(obj, "item") and callable(obj.item):
        return encode_rpc_value(obj.item())
    raise TypeError(f"Object of type {type(obj).__name__} is not RPC-serializable")


def decode_rpc_value(obj: Any) -> Any:
    """Decode JSON values produced by :func:`encode_rpc_value`."""
    if isinstance(obj, dict):
        if "__bytes__" in obj and len(obj) == 1:
            return base64.b64decode(obj["__bytes__"])
        if "__ndarray__" in obj and "dtype" in obj and "shape" in obj:
            import numpy as np

            raw = base64.b64decode(obj["__ndarray__"])
            return np.frombuffer(raw, dtype=np.dtype(obj["dtype"])).reshape(obj["shape"]).copy()
        return {k: decode_rpc_value(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode_rpc_value(v) for v in obj]
    return obj


class RpcServer:
    """Thread-pooled HTTP RPC server exposing registered methods at ``POST /call``."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, name: str = "rpc") -> None:
        self.name = name
        self._methods: Dict[str, Callable[..., Any]] = {}
        self._lock = threading.RLock()
        parent = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return  # Keep test and CLI output clean

            def _write_json(self, status: int, body: Dict[str, Any]) -> None:
                raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                if self.path == "/healthz":
                    self._write_json(
                        200,
                        {
                            "ok": True,
                            "service": parent.name,
                            "methods": sorted(parent._methods.keys()),
                        },
                    )
                else:
                    self._write_json(404, {"ok": False, "error": "not_found"})

            def do_POST(self) -> None:
                if self.path != "/call":
                    self._write_json(404, {"ok": False, "error": "not_found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    method = str(body.get("method", ""))
                    kwargs = decode_rpc_value(body.get("kwargs") or {})
                    fn = parent._methods.get(method)
                    if fn is None:
                        self._write_json(400, {"ok": False, "error": f"unknown_method:{method}"})
                        return
                    result = fn(**kwargs)
                    self._write_json(200, {"ok": True, "result": encode_rpc_value(result)})
                except Exception as exc:
                    self._write_json(
                        500,
                        {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    )

        self._httpd = ThreadingHTTPServer((host, int(port)), _Handler)
        self._httpd.daemon_threads = True
        self._thread: Optional[threading.Thread] = None

    @property
    def endpoint(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def register(self, name: str, fn: Callable[..., Any]) -> None:
        with self._lock:
            self._methods[str(name)] = fn

    def start_background(self) -> str:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._httpd.serve_forever,
                name=f"rpc-{self.name}",
                daemon=True,
            )
            self._thread.start()
        return self.endpoint

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


class RpcClient:
    """Client for :class:`RpcServer` endpoints (`http://127.0.0.1:<port>`)."""

    def __init__(self, endpoint: str, timeout_s: float = 15.0) -> None:
        ep = endpoint.strip().rstrip("/")
        if "://" not in ep:
            ep = "http://" + ep
        self.endpoint = ep
        self.timeout_s = float(timeout_s)

    def healthz(self) -> Dict[str, Any]:
        req = urllib.request.Request(self.endpoint + "/healthz", method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def call(self, method: str, **kwargs: Any) -> Any:
        payload = json.dumps(
            {"method": str(method), "kwargs": encode_rpc_value(kwargs)},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint + "/call",
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw_err = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw_err)
                raise RpcError(parsed.get("error", raw_err)) from exc
            except ValueError:
                raise RpcError(f"HTTP {exc.code}: {raw_err}") from exc
        except urllib.error.URLError as exc:
            raise RpcError(f"Connection failed ({self.endpoint}): {exc}") from exc

        if not data.get("ok"):
            raise RpcError(str(data.get("error", "rpc_failed")))
        return decode_rpc_value(data.get("result"))
