"""Contract tests for the fail-closed OpenFang token-classification path."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


_BRAIN_ROOT = Path(__file__).resolve().parents[1]
_SHARED_SRC = _BRAIN_ROOT.parents[1] / "shared" / "src"
for _path in (str(_BRAIN_ROOT), str(_SHARED_SRC)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _router_module():
    sys.modules.pop("core.ollama_llm_router", None)
    return importlib.import_module("core.ollama_llm_router")


def _openfang_client(response_text: str = "ACTION") -> SimpleNamespace:
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=response_text))]
    )
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: completion)
        )
    )


def test_token_router_uses_fixed_shared_openfang_role(monkeypatch: pytest.MonkeyPatch) -> None:
    router_module = _router_module()
    requested_roles: list[str] = []

    def get_client_sync(role: str) -> SimpleNamespace:
        requested_roles.append(role)
        return _openfang_client()

    monkeypatch.setattr(router_module, "get_client_sync", get_client_sync)
    monkeypatch.setattr(router_module, "get_model", lambda role: "openfang:brain-fallback")

    router = router_module.OllamaLLMRouter()

    assert requested_roles == ["brain_fast_reasoning"]
    assert router.classify_token("deploy")["class"] == "ACTION"


def test_token_router_wraps_shared_configuration_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    router_module = _router_module()
    monkeypatch.setattr(
        router_module,
        "get_client_sync",
        lambda _role: (_ for _ in ()).throw(ValueError("missing OpenFang URL")),
    )

    with pytest.raises(router_module.OpenFangUnavailable, match="configuration"):
        router_module.OllamaLLMRouter()


def test_token_adapter_propagates_openfang_configuration_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    router_module = _router_module()
    adapter_module = importlib.import_module("core.token_frequency_adapter")
    oscillator_module = importlib.import_module("core.action_potential_oscillator")

    class UnavailableRouter:
        def __init__(self, _config: object) -> None:
            raise router_module.OpenFangUnavailable("OpenFang configuration unavailable")

    monkeypatch.setattr(router_module, "OllamaLLMRouter", UnavailableRouter)

    with pytest.raises(router_module.OpenFangUnavailable):
        adapter_module.TokenFrequencyAdapter(
            oscillator_module.ActionPotentialOscillator(),
            use_ollama=True,
        )


def test_token_adapter_does_not_hide_openfang_unavailability() -> None:
    router_module = _router_module()
    adapter_module = importlib.import_module("core.token_frequency_adapter")
    oscillator_module = importlib.import_module("core.action_potential_oscillator")

    class UnavailableRouter:
        def classify_token(self, _token: str) -> dict[str, str]:
            raise router_module.OpenFangUnavailable("OpenFang unavailable")

    adapter = adapter_module.TokenFrequencyAdapter(
        oscillator_module.ActionPotentialOscillator(),
        llm_router=UnavailableRouter(),
        use_local_fallback=False,
        use_ollama=False,
        enable_security_checks=False,
    )
    adapter._using_ollama = True

    with pytest.raises(router_module.OpenFangUnavailable):
        adapter.process_token_sync("unclassified-token")


def test_async_token_adapter_does_not_hide_openfang_unavailability() -> None:
    router_module = _router_module()
    adapter_module = importlib.import_module("core.token_frequency_adapter")
    oscillator_module = importlib.import_module("core.action_potential_oscillator")

    class UnavailableRouter:
        def route(self, **_kwargs: object) -> str:
            raise router_module.OpenFangUnavailable("OpenFang unavailable")

    adapter = adapter_module.TokenFrequencyAdapter(
        oscillator_module.ActionPotentialOscillator(),
        llm_router=UnavailableRouter(),
        use_local_fallback=False,
        use_ollama=False,
        enable_security_checks=False,
    )

    with pytest.raises(router_module.OpenFangUnavailable):
        asyncio.run(adapter.process_token("unclassified-token"))
