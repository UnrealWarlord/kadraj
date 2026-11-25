"""Sağlayıcı tabanı: Bağlantıyı Test Et, hata kartları, kayıt tablosu, fallback zinciri (ağ yok: MockTransport)."""
from __future__ import annotations

import httpx
import pytest

from kadraj.ai_engine.providers import base as B
from kadraj.ai_engine.providers.base import (BaseProvider, ModuleProvider, ProviderHTTPError, ProviderRegistry,
                                             backoff_delay, default_registry, http_status, parse_retry_after,
                                             redact)
from kadraj.core.contracts import (PingResult, Provider, ProviderKind, ProviderRegistryAPI, Severity, SmartError)
from kadraj.core.stubs import InMemoryVault
from kadraj.storage.vault import MemoryKeyring, Vault

KEY = "sk-live-VERYSECRET0987654321"


def provider(handler, *, id="anthropic", auth="header:x-api-key", local=False, needs_key=True, **kw):
    return BaseProvider(id, id.title(), kw.pop("kind", ProviderKind.LLM), needs_key=needs_key, local=local,
                        base_url="https://api.example.test", ping_path="/v1/models", auth=auth,
                        transport=httpx.MockTransport(handler), **kw)


@pytest.fixture()
def vault():
    v = InMemoryVault()
    v.set_secret("anthropic", KEY)
    return v


def _is_net_code(code: str) -> bool:
    """Haiku'nun hata sözlüğü kod adlarını değiştirebilir; ağ hatası ``net.*``/``http.*`` ailesindedir."""
    return code.startswith(("net.", "http.", "ytdlp."))


def ok_handler(request):
    return httpx.Response(200, json={"ok": True})


# ---- test_connection ----------------------------------------------------------------------------------
def test_success_reports_latency_and_sends_auth(vault):
    seen = {}

    def h(request):
        seen.update(request.headers)
        seen["url"] = str(request.url)
        seen["timeout"] = request.extensions["timeout"]
        return httpx.Response(200)

    p = provider(h, extra_headers={"anthropic-version": "2023-06-01"})
    res = p.test_connection(vault, timeout=4.0)
    assert isinstance(res, PingResult) and res.ok and res.error is None
    assert res.latency_ms is not None and res.latency_ms >= 0 and "ms" in res.message
    assert seen["x-api-key"] == KEY and seen["anthropic-version"] == "2023-06-01"
    assert seen["url"] == "https://api.example.test/v1/models"
    assert seen["timeout"]["read"] == 4.0 and seen["timeout"]["connect"] == 3.0            # zaman aşımı uygulanır
    assert p.ping(vault, 4.0).ok                                                           # ping = test_connection


@pytest.mark.parametrize("auth, check", [
    ("bearer", lambda r: r.headers["authorization"] == f"Bearer {KEY}"),
    ("token", lambda r: r.headers["authorization"] == f"Token {KEY}"),
    ("header:xi-api-key", lambda r: r.headers["xi-api-key"] == KEY),
    ("query:key", lambda r: r.url.params["key"] == KEY and "authorization" not in r.headers),
])
def test_auth_schemes(vault, auth, check):
    calls = []

    def h(r):
        calls.append(check(r))
        return httpx.Response(200)
    assert provider(h, auth=auth).test_connection(vault).ok and calls == [True]


def test_no_key_never_touches_the_network():
    calls = []
    p = provider(lambda r: calls.append(r) or httpx.Response(200))
    res = p.test_connection(InMemoryVault())
    assert not res.ok and res.latency_ms is None and res.error.code == "provider.no_key"
    assert res.error.actions[0].kind == "open_settings" and res.error.actions[0].payload == {"section": "vault"}
    assert p.test_connection(None).error.code == "provider.no_key" and calls == []


@pytest.mark.parametrize("status, code, retryable", [
    (401, "provider.auth", False), (403, "provider.auth", False), (404, "provider.not_found", False),
    (429, "provider.rate_limited", True), (500, "provider.server", True), (503, "provider.server", True),
    (418, "provider.http", False), (302, "provider.http", False)])
def test_http_statuses_become_cards(vault, status, code, retryable):
    res = provider(lambda r: httpx.Response(status, headers={"retry-after": "7"})).test_connection(vault)
    assert not res.ok and res.error.code == code and res.error.retryable is retryable
    assert res.latency_ms is not None and res.message == res.error.summary
    if status == 429:
        assert res.error.severity is Severity.WARNING and "7s" in res.error.technical


def test_redirects_are_not_followed(vault):
    calls = []

    def h(r):
        calls.append(str(r.url))
        return httpx.Response(302, headers={"location": "https://evil.test/steal"})
    res = provider(h).test_connection(vault)
    assert calls == ["https://api.example.test/v1/models"] and res.error.code == "provider.http"


def test_offline_and_timeout_become_smart_errors_with_no_secret_leak(vault):
    def refuse(r):
        raise httpx.ConnectError(f"all connection attempts failed for {r.url} key={KEY}", request=r)

    def slow(r):
        raise httpx.ReadTimeout(f"timed out {KEY}", request=r)
    off = provider(refuse, auth="query:key").test_connection(vault)
    assert not off.ok and off.error.retryable and off.latency_ms is None and _is_net_code(off.error.code)
    tmo = provider(slow).test_connection(vault)
    assert not tmo.ok and tmo.error.retryable and "timeout" in tmo.error.code
    for res in (off, tmo):
        blob = " ".join([res.message, res.error.title, res.error.summary, res.error.technical, repr(res.error)])
        assert KEY not in blob and "VERYSECRET" not in blob


def test_real_httpx_with_sockets_blocked_is_offline_not_a_crash(vault, no_network):
    p = BaseProvider("anthropic", "Anthropic", ProviderKind.LLM, base_url="https://api.example.test",
                     ping_path="/v1/models", auth="bearer")                 # transport yok → gerçek httpx
    res = p.test_connection(vault, timeout=1.0)
    assert not res.ok and _is_net_code(res.error.code) and res.error.retryable and no_network   # denendi, engellendi


def test_local_service_down_says_not_running_not_no_internet():
    def refuse(r):
        raise httpx.ConnectError("refused", request=r)
    res = provider(refuse, id="ollama", local=True, needs_key=False, auth="none").test_connection(None)
    assert res.error.code == "provider.local_down" and "Ollama" in res.error.title and res.error.retryable


def test_local_provider_needs_no_vault():
    res = provider(ok_handler, id="comfyui", local=True, needs_key=False, auth="none").test_connection(None)
    assert res.ok


def test_locked_vault_is_reported_not_raised(tmp_path):
    from kadraj.storage.vault import Vault
    v = Vault(tmp_path / "v.bin", keyring_backend=type("F", (), {
        "get_password": lambda *a: (_ for _ in ()).throw(RuntimeError()),
        "set_password": lambda *a: (_ for _ in ()).throw(RuntimeError()),
        "delete_password": lambda *a: None})(), kdf_iterations=1000)
    v.initialize("parola-12345")
    v.set_secret("anthropic", KEY)
    locked = Vault(v.path, keyring_backend=v._kr_backend, kdf_iterations=1000)
    p = provider(ok_handler)
    assert p.is_configured(locked) is False
    res = p.test_connection(locked)
    assert not res.ok and res.error.code == "vault.locked"


def test_custom_base_url_from_settings(vault):
    class S:
        def get(self, key, default=None):
            return "http://127.0.0.1:9999/proxy/" if key == "providers.anthropic.base_url" else default
    urls = []
    p = provider(lambda r: urls.append(str(r.url)) or httpx.Response(200), settings=S())
    assert p.test_connection(vault).ok and urls == ["http://127.0.0.1:9999/proxy/v1/models"]


def test_unset_address_is_a_misconfiguration_card(vault):
    p = BaseProvider("x", "X", ProviderKind.LLM, auth="none", needs_key=False, transport=httpx.MockTransport(ok_handler))
    assert p.test_connection(None).error.code == "provider.misconfigured"


def test_module_provider():
    ok = ModuleProvider("w", "Whisper", ProviderKind.STT, "json").test_connection()
    assert ok.ok
    bad = ModuleProvider("w", "Whisper", ProviderKind.STT, "no_such_module_zzz").test_connection()
    assert not bad.ok and bad.error.code == "provider.module_missing"


# ---- yardımcılar ------------------------------------------------------------------------------------------------------
def test_redact_removes_keys_and_query_strings():
    assert KEY not in redact(f"GET https://x.test/api?key={KEY}&q=1 failed with {KEY}", KEY)
    assert "?…" in redact("https://x.test/a?token=abc", None)
    assert redact("kısa", "abc") == "kısa"                                         # çok kısa anahtar yok sayılır


def test_parse_retry_after_and_backoff():
    assert parse_retry_after("7") == 7.0 and parse_retry_after("0") == 0.0 and parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None and parse_retry_after("-5") == 0.0
    assert parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT") > 1000
    assert [backoff_delay(i) for i in range(4)] == [1.0, 2.0, 4.0, 8.0] and backoff_delay(10) == 30.0
    assert backoff_delay(3, retry_after=2) == 2 and backoff_delay(0, retry_after=999) == 30.0


def test_http_status_extraction():
    assert http_status(ProviderHTTPError(429)) == 429
    req = httpx.Request("GET", "https://x.test")
    assert http_status(httpx.HTTPStatusError("x", request=req, response=httpx.Response(503, request=req))) == 503
    assert http_status(ValueError("x")) is None


# ---- kayıt tablosu ----------------------------------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_registry(settings=None, jobs=None):
    clock, sleeps = Clock(), []

    def sleep(sec):
        sleeps.append(sec)
        clock.now += sec
    reg = ProviderRegistry(settings, jobs, sleep=sleep, clock=clock)
    return reg, clock, sleeps


class FakeSettings:
    def __init__(self, order=None):
        self.order = order or {}

    def get(self, key, default=None):
        return self.order if key == "fallback_order" else default


def fake(id, kind=ProviderKind.LLM, local=False, key=True):
    return BaseProvider(id, id, kind, needs_key=key, local=local, base_url="http://x.test", ping_path="/",
                        transport=httpx.MockTransport(ok_handler))


def test_register_get_list_replace():
    reg, *_ = make_registry()
    a = fake("a")
    reg.register(a)
    with pytest.raises(ValueError):
        reg.register(fake("a"))
    b = fake("a")
    reg.register(b, replace=True)
    assert reg.get("a") is b and reg.get("zzz") is None
    reg.register(fake("img", ProviderKind.IMAGE))
    assert [p.id for p in reg.list(ProviderKind.IMAGE)] == ["img"] and len(reg.list()) == 2
    assert isinstance(reg, ProviderRegistryAPI) and isinstance(a, Provider)


def test_configured_requires_keys_for_clouds_only():
    reg, *_ = make_registry()
    for p in (fake("cloud1"), fake("cloud2"), fake("local1", local=True, key=False)):
        reg.register(p)
    v = InMemoryVault()
    v.set_secret("cloud2", "k" * 10)
    assert [p.id for p in reg.configured(v)] == ["cloud2", "local1"]
    assert [p.id for p in reg.configured(None)] == ["local1"]


def test_default_chain_is_cloud_then_local_in_registration_order():
    reg, *_ = make_registry()
    v = InMemoryVault()
    for pid in ("ollama", "anthropic", "openai"):
        reg.register(fake(pid, local=(pid == "ollama"), key=(pid != "ollama")))
        if pid != "ollama":
            v.set_secret(pid, "k" * 10)
    assert [p.id for p in reg.chain(ProviderKind.LLM, v)] == ["anthropic", "openai", "ollama"]


def test_chain_honours_fallback_order_setting_and_skips_unconfigured():
    reg, *_ = make_registry(FakeSettings({"llm": ["ollama", "openai", "ghost", "anthropic"]}))
    v = InMemoryVault()
    for pid in ("anthropic", "openai"):
        reg.register(fake(pid))
        v.set_secret(pid, "k" * 10)
    reg.register(fake("ollama", local=True, key=False))
    reg.register(fake("unkeyed"))
    assert [p.id for p in reg.chain(ProviderKind.LLM, v)] == ["ollama", "openai", "anthropic"]


def test_cooling_providers_are_skipped_until_the_time_passes():
    reg, clock, _ = make_registry()
    reg.register(fake("a", local=True, key=False))
    reg.cool_down("a", 30)
    assert reg.is_cooling("a") and reg.cooling_remaining("a") == 30 and reg.chain(ProviderKind.LLM) == []
    clock.now = 31
    assert not reg.is_cooling("a") and [p.id for p in reg.chain(ProviderKind.LLM)] == ["a"]


def test_registry_test_connection_wrappers(vault):
    reg, *_ = make_registry()
    pings = []
    reg.on_ping = lambda pid, ms: pings.append((pid, ms))
    reg.register(provider(ok_handler))
    assert reg.test_connection("anthropic", vault).ok and pings[0][0] == "anthropic" and pings[0][1] >= 0
    unknown = reg.test_connection("nope")
    assert not unknown.ok and unknown.error.code == "provider.unknown"

    class Bad:
        id, display_name, kind, needs_key, local = "bad", "Bad", ProviderKind.LLM, False, True

        def is_configured(self, v): return True
        def ping(self, v, timeout=5.0): raise RuntimeError("kötü sağlayıcı")
    reg.register(Bad())
    res = reg.test_connection("bad")
    assert not res.ok and res.error.code == "app.unexpected" and pings[-1] == ("bad", None)
    reg.on_ping = lambda *a: 1 / 0                                                  # kötü geri çağrı da çökertmez
    assert reg.test_connection("anthropic", vault).ok


def test_default_registry_covers_the_plan_services():
    reg = default_registry()
    ids = {p.id for p in reg.list()}
    assert ids == {"anthropic", "openai_compat", "bfl_flux", "google_imagen", "elevenlabs", "pexels", "pixabay",
                   "freesound", "ollama", "comfyui", "whisper_local"}
    assert reg.get("ollama").local and not reg.get("ollama").needs_key and reg.get("anthropic").needs_key
    assert reg.get("whisper_local").kind is ProviderKind.STT
    assert {p.id for p in reg.list(ProviderKind.STOCK)} == {"pexels", "pixabay", "freesound"}


def test_default_registry_pings_through_mock_transport():
    seen = {}

    def h(r):
        seen[r.url.host] = dict(r.headers) | {"_q": dict(r.url.params)}
        return httpx.Response(200)
    reg = default_registry(transport=httpx.MockTransport(h))
    v = InMemoryVault()
    for pid in ("anthropic", "pixabay", "freesound", "pexels"):
        v.set_secret(pid, f"key-{pid}-123456")
    assert reg.test_connection("anthropic", v).ok and reg.test_connection("pixabay", v).ok
    assert reg.test_connection("freesound", v).ok and reg.test_connection("ollama", v).ok
    assert seen["api.anthropic.com"]["x-api-key"] == "key-anthropic-123456"
    assert seen["pixabay.com"]["_q"]["key"] == "key-pixabay-123456"
    assert seen["freesound.org"]["authorization"] == "Token key-freesound-123456"
    assert "127.0.0.1" in seen


# ---- fallback zinciri -----------------------------------------------------------------------------------------------------
def chain_setup(jobs=None, settings=None):
    reg, clock, sleeps = make_registry(settings, jobs)
    v = InMemoryVault()
    for pid, local in (("cloud1", False), ("cloud2", False), ("local1", True)):
        reg.register(fake(pid, local=local, key=not local))
        if not local:
            v.set_secret(pid, "k" * 10)
    return reg, v, clock, sleeps


class RecJobs:
    def __init__(self):
        self.submitted = []

    def submit(self, kind, payload=None, title=""):
        self.submitted.append((kind, payload, title))
        return type("J", (), {"id": 1, "kind": kind})()


def test_first_provider_success_short_circuits():
    reg, v, *_ = chain_setup()
    seen = []
    res = reg.call_with_fallback(ProviderKind.LLM, lambda p: seen.append(p.id) or "cevap", vault=v)
    assert res.ok and res.value == "cevap" and res.provider_id == "cloud1" and seen == ["cloud1"]
    assert [a.provider_id for a in res.attempts] == ["cloud1"] and res.error is None and res.queued_job is None


def test_failure_moves_to_next_provider():
    reg, v, *_ = chain_setup()

    def op(p):
        if p.id == "cloud1":
            raise ProviderHTTPError(500, "boom")
        return p.id
    res = reg.call_with_fallback(ProviderKind.LLM, op, vault=v)
    assert res.ok and res.provider_id == "cloud2" and res.attempts[0].error is not None


def test_offline_skips_remaining_clouds_and_uses_local():
    reg, v, *_ = chain_setup()
    called = []

    def op(p):
        called.append(p.id)
        if not p.local:
            raise ConnectionError("no route")
        return "yerel cevap"
    res = reg.call_with_fallback(ProviderKind.LLM, op, vault=v)
    assert res.ok and res.provider_id == "local1" and called == ["cloud1", "local1"]       # cloud2 hiç çağrılmadı
    assert [(a.provider_id, a.skipped) for a in res.attempts] == [("cloud1", False), ("cloud2", True), ("local1", False)]
    assert _is_net_code(res.attempts[0].error.code)


def test_429_backs_off_with_retry_after_then_succeeds():
    reg, v, clock, sleeps = chain_setup()
    n = {"c": 0}

    def op(p):
        n["c"] += 1
        if n["c"] < 3:
            raise ProviderHTTPError(429, "slow down", retry_after=5)
        return "tamam"
    res = reg.call_with_fallback(ProviderKind.LLM, op, vault=v)
    assert res.ok and res.provider_id == "cloud1" and sleeps == [5.0, 5.0] and res.attempts[-1].retries == 2


def test_429_without_header_uses_exponential_backoff_then_cools_down_and_falls_through():
    reg, v, clock, sleeps = chain_setup()

    def op(p):
        if p.id == "cloud1":
            raise ProviderHTTPError(429, "limit")
        return p.id
    res = reg.call_with_fallback(ProviderKind.LLM, op, vault=v)
    assert sleeps == [1.0, 2.0] and res.provider_id == "cloud2"
    assert reg.is_cooling("cloud1")                                              # sıradaki çağrılarda atlanır
    assert [p.id for p in reg.chain(ProviderKind.LLM, v)] == ["cloud2", "local1"]
    clock.now += 600
    assert reg.chain(ProviderKind.LLM, v)[0].id == "cloud1"


def test_all_fail_is_queued_when_a_payload_is_given():
    jobs = RecJobs()
    reg, v, *_ = chain_setup(jobs)
    res = reg.call_with_fallback(ProviderKind.LLM, lambda p: (_ for _ in ()).throw(ConnectionError("x")),
                                 vault=v, queue_payload={"prompt": "merhaba"}, queue_title="Sonra dene")
    assert not res.ok and res.value is None and _is_net_code(res.error.code)
    assert res.queued_job is not None
    kind, payload, title = jobs.submitted[0]
    assert kind == "ai.deferred" and payload["kind"] == "llm" and payload["payload"] == {"prompt": "merhaba"}
    assert _is_net_code(payload["reason"]) and title == "Sonra dene"


def test_all_fail_without_payload_is_not_queued():
    jobs = RecJobs()
    reg, v, *_ = chain_setup(jobs)
    res = reg.call_with_fallback(ProviderKind.LLM, lambda p: 1 / 0, vault=v)
    assert not res.ok and res.queued_job is None and jobs.submitted == []
    assert len(res.attempts) == 3 and all(a.error for a in res.attempts)


def test_no_providers_at_all_gives_a_clear_card_and_still_queues():
    jobs = RecJobs()
    reg, *_ = make_registry(None, jobs)
    res = reg.call_with_fallback(ProviderKind.IMAGE, lambda p: 1, queue_payload={"a": 1})
    assert not res.ok and res.error.code == "provider.none_available" and res.queued_job is not None


def test_queue_failure_does_not_raise():
    class BadJobs:
        def submit(self, *a, **k):
            raise RuntimeError("kuyruk yok")
    reg, v, *_ = chain_setup(BadJobs())
    res = reg.call_with_fallback(ProviderKind.LLM, lambda p: 1 / 0, vault=v, queue_payload={"x": 1})
    assert not res.ok and res.queued_job is None and res.error is not None


def test_provider_only_with_key_in_a_real_vault_is_used(tmp_path):
    v = Vault(tmp_path / "v.bin", keyring_backend=MemoryKeyring(), kdf_iterations=1000)
    reg, *_ = make_registry()
    reg.register(fake("cloud1"))
    assert reg.chain(ProviderKind.LLM, v) == []
    v.set_secret("cloud1", "k" * 10)
    assert [p.id for p in reg.chain(ProviderKind.LLM, v)] == ["cloud1"]
