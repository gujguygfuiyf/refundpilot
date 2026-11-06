"""Provider wiring and the reference-image cache (no network)."""
from types import SimpleNamespace

import httpx

from app import ai
from tests.conftest import jpeg_bytes


def cfg(**kw):
    base = dict(ai_provider="gemini", gemini_api_key="", gemini_model="gemini-flash-latest",
                openai_api_key="", openai_model="gpt-5")
    return SimpleNamespace(**{**base, **kw})


def names(assessor):
    return [p.name for p in assessor.providers]


def test_no_keys_means_offline_fake():
    assert isinstance(ai.build_assessor(cfg()), ai.FakeAssessor)


def test_gemini_is_primary_and_openai_the_backup():
    both = dict(gemini_api_key="g", openai_api_key="o")
    assert names(ai.build_assessor(cfg(**both))) == ["gemini", "openai"]
    assert names(ai.build_assessor(cfg(ai_provider="auto", **both))) == ["gemini", "openai"]
    assert names(ai.build_assessor(cfg(ai_provider="openai", **both))) == ["openai", "gemini"]
    assert names(ai.build_assessor(cfg(gemini_api_key="g"))) == ["gemini"]


def test_reference_fetch_failure_is_not_cached(monkeypatch):
    url = "https://example.test/listing.jpg"
    ai._REFERENCE_CACHE.pop(url, None)
    calls = {"n": 0}

    def flaky_get(u, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("blip")
        return httpx.Response(200, content=jpeg_bytes(), request=httpx.Request("GET", u))

    monkeypatch.setattr(ai.httpx, "get", flaky_get)
    assert ai.fetch_reference(url) is None            # first call fails...
    assert ai.fetch_reference(url) is not None        # ...and the next one tries again
    assert ai.fetch_reference(url) is not None and calls["n"] == 2   # successes are cached


def test_gemini_client_pins_the_api_version(monkeypatch):
    """google-genai 3.0 switched the default to v1, where gemini-flash-latest 404s; the live demo broke on redeploy."""
    from google import genai
    from app.ai import GeminiAssessor
    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(genai, "Client", FakeClient)
    GeminiAssessor("key", "gemini-flash-latest")
    assert seen["http_options"].api_version == "v1beta"
