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


# ----------------------------------------------------------------- retries on transient provider failures

class _ApiError(Exception):
    """Shaped like google.genai.errors.APIError: carries the HTTP status in `code`."""
    def __init__(self, code):
        super().__init__(f"{code} from the model API")
        self.code = code


class _Flaky:
    name = "gemini"

    def __init__(self, failures):
        self.failures, self.calls = list(failures), 0

    def assess(self, inp):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return ai.FakeAssessor().assess(inp)


def _inp():
    return ai.AssessmentInput(product_name="Mug", product_image_url="https://example.test/mug.jpg",
                              buyer_photos=[jpeg_bytes()], buyer_text="broken", order_cents=1900, days_since_delivery=3)


def test_transient_classification():
    assert all(ai.is_transient(_ApiError(c)) for c in (408, 429, 500, 502, 503, 504))
    assert not any(ai.is_transient(_ApiError(c)) for c in (400, 401, 403, 404))
    assert ai.is_transient(httpx.ReadTimeout("slow")) and ai.is_transient(httpx.ConnectError("down"))
    assert ai.is_transient(TimeoutError()) and not ai.is_transient(ValueError("bad schema"))


def test_overloaded_model_is_retried_and_then_succeeds():
    """A single 503 "model is overloaded" used to send a perfectly clear claim to manual review."""
    flaky, pauses = _Flaky([_ApiError(503), _ApiError(429)]), []
    chain = ai.ChainAssessor([flaky], retry_delays_s=(1.5, 4.0), sleep=pauses.append)
    r = chain.assess(_inp())
    assert r.provider == "fake" and r.assessment.damage_type == "broken"
    assert flaky.calls == 3 and pauses == [1.5, 4.0]


def test_retries_are_bounded_then_the_backup_or_a_human_takes_over():
    primary, backup, pauses = _Flaky([_ApiError(503)] * 5), _Flaky([]), []
    r = ai.ChainAssessor([primary, backup], retry_delays_s=(1.5, 4.0), sleep=pauses.append).assess(_inp())
    assert primary.calls == 3 and backup.calls == 1 and r.assessment.damage_type == "broken"

    alone = _Flaky([_ApiError(503)] * 5)
    r = ai.ChainAssessor([alone], retry_delays_s=(1.5, 4.0), sleep=pauses.append).assess(_inp())
    assert alone.calls == 3 and r.provider == "none" and r.assessment.needs_human_review
    assert "after 3 attempts" in r.assessment.limitations[0]


def test_permanent_errors_are_not_retried():
    bad_key, pauses = _Flaky([_ApiError(401)]), []
    r = ai.ChainAssessor([bad_key], sleep=pauses.append).assess(_inp())
    assert bad_key.calls == 1 and pauses == [] and r.provider == "none"


def test_gemini_client_sets_a_request_timeout(monkeypatch):
    from google import genai
    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(genai, "Client", FakeClient)
    ai.GeminiAssessor("key", "gemini-flash-latest")
    assert seen["http_options"].timeout == ai.GEMINI_TIMEOUT_MS
