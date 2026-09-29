"""Contract tests for the deterministic, read-only Rowboat status MCP tool."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[3]
SERVER_PATH = ROOT / "spaces" / "rowboat" / "mcp_server.py"


def load_server():
    if not SERVER_PATH.is_file():
        raise AssertionError("spaces/rowboat/mcp_server.py must be versioned")
    spec = importlib.util.spec_from_file_location("spaces_rowboat_mcp_server", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("Rowboat MCP server must be importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call_status(server, *, request_id=7, arguments=None):
    return server.handle_message({
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": "rowboat_status", "arguments": arguments or {}},
    })


def _payload(response):
    return json.loads(response["result"]["content"][0]["text"])


def test_tools_list_exposes_only_a_closed_deterministic_status_tool():
    server = load_server()
    response = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tool, = response["result"]["tools"]
    assert tool["name"] == "rowboat_status"
    assert tool["inputSchema"] == {"type": "object", "properties": {}, "additionalProperties": False}
    assert "OPENAI" not in json.dumps(response)


def test_status_requires_explicit_nonblank_nonlocal_url_without_default():
    server = load_server()
    for configured_env in ({}, {"ROWBOAT_URL": "   "}, {"ROWBOAT_URL": "http://localhost:3000"}):
        with mock.patch.dict(os.environ, configured_env, clear=True):
            response = call_status(server)
        assert response["result"]["isError"] is True
        assert "ROWBOAT_URL" in response["result"]["content"][0]["text"]


def test_status_rejects_loopback_and_noncanonical_numeric_hosts():
    server = load_server()
    for url in (
        "http://127.0.0.2", "http://[::1]", "http://[::ffff:127.0.0.1]",
        "http://rowboat.localhost", "http://2130706433", "http://0x7f000001",
        "http://127.1", "http://0177.0.0.1",
    ):
        with mock.patch.dict(os.environ, {"ROWBOAT_URL": url}, clear=True):
            response = call_status(server)
        assert response["result"]["isError"] is True, url
        assert "ROWBOAT_URL" in response["result"]["content"][0]["text"], url


def test_status_rejects_url_userinfo_without_returning_it():
    server = load_server()
    secret_url = "https://user:secret@rowboat.example"
    with mock.patch.dict(os.environ, {"ROWBOAT_URL": secret_url}, clear=True):
        response = call_status(server, request_id="credential-check")
    assert response["id"] == "credential-check"
    assert response["result"]["isError"] is True
    assert "secret" not in response["result"]["content"][0]["text"]


def test_status_rejects_unknown_arguments_instead_of_ignoring_them():
    server = load_server()
    response = call_status(server, request_id="args", arguments={"ignored": True})
    assert response["id"] == "args"
    assert response["result"]["isError"] is True
    assert "arguments" in response["result"]["content"][0]["text"]


def test_malformed_url_returns_stable_tool_error_with_original_request_id():
    server = load_server()
    with mock.patch.dict(os.environ, {"ROWBOAT_URL": "http://[::1"}, clear=True):
        response = call_status(server, request_id="malformed-url")
    assert response["id"] == "malformed-url"
    assert response["result"]["isError"] is True
    assert response["result"]["content"][0]["text"] == '{"error": "configuration_error: ROWBOAT_URL is invalid"}'


def test_status_rejects_query_and_fragment_without_echoing_secret_or_calling_http():
    server = load_server()
    for url in (
        "https://rowboat.example/?token=secret-query",
        "https://rowboat.example/#secret-fragment",
    ):
        with mock.patch.dict(os.environ, {"ROWBOAT_URL": url}, clear=True), mock.patch.object(
            server, "_open_head_without_redirect", side_effect=AssertionError("must not probe")
        ):
            response = call_status(server, request_id="secret-url")
        assert response["id"] == "secret-url"
        assert response["result"]["isError"] is True
        text = response["result"]["content"][0]["text"]
        assert text == '{"error": "configuration_error: ROWBOAT_URL is invalid"}'
        assert "secret" not in text


def test_status_rejects_unspecified_and_mapped_unspecified_ip_endpoints():
    server = load_server()
    for url in ("http://0.0.0.0", "http://[::]", "http://[::ffff:0.0.0.0]"):
        with mock.patch.dict(os.environ, {"ROWBOAT_URL": url}, clear=True), mock.patch.object(
            server, "_open_head_without_redirect", side_effect=AssertionError("must not probe")
        ):
            response = call_status(server, request_id="unspecified")
        assert response["id"] == "unspecified"
        assert response["result"]["isError"] is True
        assert response["result"]["content"][0]["text"] == '{"error": "configuration_error: ROWBOAT_URL is invalid"}'


def test_status_uses_head_and_marks_2xx_and_3xx_as_verified():
    server = load_server()
    seen = []

    class Response:
        def __init__(self, status): self.status = status
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def getcode(self): return self.status

    class Opener:
        def open(self, request, timeout):
            seen.append((request.get_method(), request.full_url, timeout))
            return Response(302)

    with mock.patch.dict(os.environ, {"ROWBOAT_URL": "https://rowboat.example/"}, clear=True), mock.patch.object(server.urllib.request, "build_opener", return_value=Opener()):
        response = call_status(server)
    assert response["result"].get("isError", False) is False
    assert _payload(response) == {"http_status": 302, "ok": True, "source": "rowboat-http", "url": "https://rowboat.example"}
    assert seen == [("HEAD", "https://rowboat.example", server.REQUEST_TIMEOUT_SECONDS)]


def test_no_redirect_helper_keeps_redirected_request_as_head():
    server = load_server()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            calls.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/target")
                self.end_headers()
                return
            self.send_response(204)
            self.end_headers()
        def do_GET(self):
            calls.append(f"GET:{self.path}")
            self.send_response(204)
            self.end_headers()
        def log_message(self, *_args):
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/redirect", method="HEAD")
        try:
            server._open_head_without_redirect(request, timeout=1)
        except urllib.error.HTTPError as exc:
            assert exc.code == 302
        else:
            raise AssertionError("3xx must remain observable instead of following the redirect")
    finally:
        httpd.shutdown()
        thread.join(timeout=2)
        httpd.server_close()
    assert calls == ["/redirect"]


def test_status_refutes_4xx_and_5xx_http_error_responses():
    server = load_server()
    for status in (404, 503):
        request = urllib.request.Request("https://rowboat.example", method="HEAD")
        error = urllib.error.HTTPError(request.full_url, status, "unavailable", {}, None)
        class Opener:
            def open(self, _request, timeout):
                raise error
        with mock.patch.dict(os.environ, {"ROWBOAT_URL": "https://rowboat.example"}, clear=True), mock.patch.object(server.urllib.request, "build_opener", return_value=Opener()):
            response = call_status(server)
        assert _payload(response) == {"http_status": status, "ok": False, "source": "rowboat-http", "url": "https://rowboat.example"}


def test_status_reports_transport_errors_without_false_success():
    server = load_server()
    class Opener:
        def open(self, _request, timeout):
            raise OSError("offline")
    with mock.patch.dict(os.environ, {"ROWBOAT_URL": "https://rowboat.example"}, clear=True), mock.patch.object(server.urllib.request, "build_opener", return_value=Opener()):
        response = call_status(server)
    assert response["result"]["isError"] is True
    assert _payload(response) == {"error": "OSError", "ok": False, "source": "rowboat-http", "url": "https://rowboat.example"}
