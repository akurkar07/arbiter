"""NimNemotron env routing: lock the NVIDIA-vs-OpenRouter fallback selection.

The reasoning layer can run against the real NVIDIA NIM endpoint or, when the
NVIDIA key has no inference entitlement, an OpenAI-compatible fallback such as
OpenRouter that hosts free Nemotron variants. The selection is driven entirely
by env vars; these tests lock that wiring without making any network call
(``from_env`` constructs the client but never calls the model).

We stub the ``openai.OpenAI`` constructor so no real client is built, and assert
which key/base_url the layer resolved.
"""

from __future__ import annotations

import pytest

from arbiter.agent import nim_nemotron
from arbiter.agent.nim_nemotron import NimNemotron, NIM_BASE_URL


@pytest.fixture(autouse=True)
def _no_real_openai(monkeypatch):
    """Replace openai.OpenAI with a recorder so no real client is constructed."""
    captured = {}

    class _FakeOpenAI:
        def __init__(self, api_key, base_url, timeout):
            captured["api_key"] = api_key
            captured["base_url"] = base_url

    import openai
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    return captured


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start each test from a known-empty inference-env state."""
    for var in ("NVIDIA_API_KEY", "NVIDIA_NIM_KEY", "OPENROUTER_API_KEY",
                "NVIDIA_NIM_BASE_URL", "NVIDIA_NIM_MODEL"):
        monkeypatch.delenv(var, raising=False)


def test_from_env_returns_none_with_no_key(monkeypatch):
    """No key anywhere -> None, so the agent falls back to the mock."""
    assert NimNemotron.from_env() is None


def test_nvidia_key_routes_to_nim(monkeypatch, _no_real_openai):
    """NVIDIA_API_KEY -> the real NVIDIA NIM endpoint, provider names it NIM."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-fake")
    nim = NimNemotron.from_env()
    assert nim is not None
    assert _no_real_openai["base_url"] == NIM_BASE_URL
    assert nim.provider == "NVIDIA NIM"


def test_openrouter_fallback_routes_to_openrouter(monkeypatch, _no_real_openai):
    """OPENROUTER_API_KEY + base-url override -> OpenRouter endpoint, named so."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    monkeypatch.setenv("NVIDIA_NIM_BASE_URL", "https://openrouter.ai/api/v1")
    nim = NimNemotron.from_env()
    assert nim is not None
    assert _no_real_openai["base_url"] == "https://openrouter.ai/api/v1"
    assert _no_real_openai["api_key"] == "or-fake"
    assert nim.provider == "OpenRouter"


def test_nvidia_key_precedence_over_openrouter(monkeypatch, _no_real_openai):
    """When both keys are set, the NVIDIA key wins (primary path first)."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-fake")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    NimNemotron.from_env()
    assert _no_real_openai["api_key"] == "nvapi-fake"


def test_nim_key_alias_is_accepted(monkeypatch, _no_real_openai):
    """NVIDIA_NIM_KEY works as an alias for NVIDIA_API_KEY."""
    monkeypatch.setenv("NVIDIA_NIM_KEY", "nvapi-alias")
    nim = NimNemotron.from_env()
    assert nim is not None
    assert _no_real_openai["api_key"] == "nvapi-alias"


def test_unknown_base_url_reports_itself(monkeypatch, _no_real_openai):
    """An unrecognised endpoint reports its raw url, not a wrong sponsor name."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setenv("NVIDIA_NIM_BASE_URL", "https://example.test/v1")
    nim = NimNemotron.from_env()
    assert nim is not None
    assert nim.provider == "https://example.test/v1"
