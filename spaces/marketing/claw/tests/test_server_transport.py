"""Pins the 20.10.2026 fix: with the sidecar bound to 127.0.0.1 the MCP library switches on DNS-rebinding
protection and only accepts localhost Host headers - the openclaw container calls via host.docker.internal and
got "Invalid Host header". The protection stays on; host.docker.internal is added to the allowed hosts."""
from spaces.marketing.claw import server


def test_loopback_bind_allows_the_container_host_and_keeps_protection(monkeypatch):
    monkeypatch.setattr(server, "HOST", "127.0.0.1")
    s = server._transport_security()
    assert s.enable_dns_rebinding_protection is True
    assert "host.docker.internal:*" in s.allowed_hosts
    assert {"127.0.0.1:*", "localhost:*"} <= set(s.allowed_hosts)
    assert "http://host.docker.internal:*" in s.allowed_origins


def test_open_bind_keeps_the_library_default(monkeypatch):
    monkeypatch.setattr(server, "HOST", "0.0.0.0")
    assert server._transport_security() is None
