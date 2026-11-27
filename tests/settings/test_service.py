"""Ayarlar servisi: kasa, sağlayıcı pingi, yerel algılama, araçlar, klasör ve önbellek, sıra, görünüm, profiller."""
from __future__ import annotations

import urllib.error  # noqa: F401 — ağ hatası senaryoları için ayrılmış
from pathlib import Path

import pytest

from kadraj.core.contracts import Topics, UiMode, PingResult, SmartError
from kadraj.modules.settings import service as S
from kadraj.storage import cache, profiles
from kadraj.storage.profiles import ProfileError

from .fakes import FULL_KEY, FakeRun, FakeVault

# 8 anahtarlı servis + video servisi (AI sağlayıcıları Ayarlar açılırken kurulur); OpenAI görsel, anahtarını OpenAI ile paylaştığı için ayrı satır olmaz
EIGHT_KEY_PROVIDERS = {"anthropic", "openai_compat", "bfl_flux", "google_imagen", "elevenlabs",
                       "pexels", "pixabay", "freesound", "video"}


# -- kasa ----------------------------------------------------------------------
def test_key_rows_cover_the_eight_key_providers_and_mask_keys(svc):
    rows = {r.provider_id: r for r in svc.key_rows()}
    assert set(rows) == EIGHT_KEY_PROVIDERS
    assert rows["anthropic"].has_key and rows["anthropic"].masked.startswith("sk-")
    assert FULL_KEY not in rows["anthropic"].masked
    assert not rows["pexels"].has_key and rows["pexels"].masked == ""
    assert "SECRETKEY" not in repr(rows)


def test_save_and_delete_key_round_trip(svc, vault):
    svc.save_key("pexels", "  pexels-test-key-0123456789  ")
    assert vault.has_secret("pexels")
    assert {r.provider_id: r for r in svc.key_rows()}["pexels"].has_key
    assert svc.delete_key("pexels") is True
    assert not vault.has_secret("pexels")


def test_empty_key_is_refused(svc):
    with pytest.raises(ValueError):
        svc.save_key("pexels", "   ")


def test_key_preview_is_never_the_full_key(svc):
    preview = svc.key_preview("anthropic")
    assert preview == "sk-ant…1234"
    assert svc.key_preview("pexels") == ""


def test_locked_vault_gives_safe_defaults(svc, ctx):
    ctx.vault = FakeVault({"anthropic": FULL_KEY}, locked=True)
    rows = {r.provider_id: r for r in svc.key_rows()}
    assert not rows["anthropic"].has_key and rows["anthropic"].masked == ""
    assert svc.key_preview("anthropic") == ""
    assert svc.vault_state()[0] == "locked"
    with pytest.raises(SmartError):
        svc.save_key("pexels", "x-key-1234567890abcdef")


# -- sağlayıcı pingi -----------------------------------------------------------
def test_connection_test_sets_led_and_emits_result(svc, ctx):
    svc.test_connection("anthropic", background=False)
    row = {r.provider_id: r for r in svc.key_rows()}["anthropic"]
    assert row.state == "ok" and row.latency_ms == 42.0


def test_failed_ping_is_a_red_led_not_a_crash(svc):
    svc.ping_fn = lambda pid: PingResult(False, None, "Yanıt vermedi.", None)
    svc.test_connection("anthropic", background=False)
    assert {r.provider_id: r for r in svc.key_rows()}["anthropic"].state == "fail"


def test_ping_exception_is_contained(svc):
    def boom(pid):
        raise RuntimeError("beklenmeyen")
    svc.ping_fn = boom
    svc.test_connection("anthropic", background=False)
    assert {r.provider_id: r for r in svc.key_rows()}["anthropic"].state == "fail"


def test_background_ping_emits_pingdone(svc, qtbot):
    with qtbot.waitSignal(svc.pingDone, timeout=5000) as blocker:
        svc.test_connection("anthropic")
    assert blocker.args[0] == "anthropic" and blocker.args[1].ok is True


# -- yerel çalışma zamanı ------------------------------------------------------
def test_local_detection_reports_up_down_and_missing(svc, monkeypatch):
    monkeypatch.setattr(svc, "_module_installed", lambda module="faster_whisper": False)
    svc.refresh_local(background=False)
    rows = {r.provider_id: r for r in svc.local_runtimes()}
    assert rows["ollama"].state == "up" and rows["ollama"].models == ("llama3", "qwen2")
    assert rows["comfyui"].state == "down"
    assert rows["whisper_local"].state == "missing"


def test_start_ollama_missing_binary(svc, monkeypatch):
    monkeypatch.setattr(S.shutil, "which", lambda _name: None)
    assert svc.start_local("ollama") == "missing"


def test_start_ollama_spawns_serve_without_shell(svc, monkeypatch):
    monkeypatch.setattr(S.shutil, "which", lambda _name: "/usr/bin/ollama")
    assert svc.start_local("ollama") == "started"
    call = svc.popen.calls[-1]
    assert call["args"] == ["/usr/bin/ollama", "serve"] and call["start_new_session"] is True
    assert "shell" not in call


def test_comfyui_needs_a_real_folder(svc, tmp_path):
    assert svc.start_local("comfyui") == "need_path"
    (tmp_path / "main.py").write_text("print('x')", encoding="utf-8")
    svc.set_comfyui_path(str(tmp_path))
    assert svc.start_local("comfyui") == "started"
    assert svc.popen.calls[-1]["cwd"] == str(tmp_path)


def test_model_choices_persist(svc):
    svc.set_model("ollama", "qwen2")
    assert svc.model_of("ollama") == "qwen2"
    svc.set_whisper_model("base")
    assert svc.whisper_model() == "base"
    with pytest.raises(ValueError):
        svc.set_whisper_model("gigantic")


# -- araçlar -------------------------------------------------------------------
def test_ffmpeg_version_detected_through_injected_runner(svc, monkeypatch):
    monkeypatch.setattr(S.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    svc.refresh_tools(background=False)
    info = svc.tool_info()
    assert info.ffmpeg == "7.1" and info.ffmpeg_path == "/usr/bin/ffmpeg"


class _FakeUpdater:
    def __init__(self, report):
        self.report, self.calls = report, 0

    def run(self, progress=None, *, force=False, **_kw):
        self.calls += 1
        assert force is True                       # "Şimdi güncelle" süre sınırını yok sayar
        if progress:
            progress("yt-dlp indiriliyor…", 0.5)
        return self.report


def test_component_update_runs_as_a_job_and_succeeds(svc, ctx):
    from kadraj.core.updater import Report
    fake = _FakeUpdater(Report(updated=["yt-dlp"], current=["deno"]))
    svc.updater_factory = lambda: fake
    job = svc.update_ytdlp()
    assert ctx.jobs.wait_idle(10000)
    done = ctx.jobs.get(job.id)
    assert done.status.value == "done" and fake.calls == 1


def test_component_update_offline_gives_numbered_steps(svc, ctx):
    from kadraj.core.updater import Report
    svc.updater_factory = lambda: _FakeUpdater(Report(offline=True))
    job = svc.update_components()
    assert ctx.jobs.wait_idle(10000)
    failed = ctx.jobs.get(job.id)
    assert failed.status.value == "failed" and failed.error


def test_component_update_error_becomes_a_failed_job(svc, ctx):
    from kadraj.core.updater import Report
    svc.updater_factory = lambda: _FakeUpdater(Report(errors=["yt-dlp: SHA-256 sağlaması uyuşmadı"]))
    job = svc.update_ytdlp()
    assert ctx.jobs.wait_idle(10000)
    failed = ctx.jobs.get(job.id)
    assert failed.status.value == "failed" and failed.error


def test_repair_is_the_update_job_run_with_a_seeded_updater(svc, ctx):
    from kadraj.core.updater import Report
    seen = []

    def factory(seed=False):
        seen.append(seed)
        return _FakeUpdater(Report(updated=["yt-dlp", "deno", "ffmpeg", "ffprobe"]))

    svc.updater_factory = factory
    job = svc.repair_components()
    assert ctx.jobs.get(job.id).payload.get("repair") is True
    assert ctx.jobs.wait_idle(10000)
    assert ctx.jobs.get(job.id).status.value == "done"
    assert seen == [True]


def test_plain_update_keeps_the_updater_unseeded(svc, ctx):
    from kadraj.core.updater import Report
    seen = []

    def factory(seed=False):
        seen.append(seed)
        return _FakeUpdater(Report(current=["yt-dlp"]))

    svc.updater_factory = factory
    svc.update_components()
    assert ctx.jobs.wait_idle(10000)
    assert seen == [False]


def test_error_card_update_button_works_before_settings_was_opened(ctx, qtbot, monkeypatch):
    """İndirme hatası kartındaki "Bileşenleri güncelle": Ayarlar sayfası hiç açılmamışken de güncelleme başlar
    (konuyu dinleyen Ayarlar servisi ana pencere kurulurken sayfalarla birlikte başlar)."""
    from kadraj.modules.downloader import errors as dl_errors
    from kadraj.ui.shell.main_window import MainWindow
    calls = []
    monkeypatch.setattr(S.SettingsService, "update_components", lambda self: calls.append(self))
    monkeypatch.setattr(dl_errors.components, "have", lambda _n: True)
    card = dl_errors.explain(RuntimeError("ERROR: Unable to extract uploader id; please report this issue"))
    update = next(a for a in card.actions if a.id == "update_components")
    win = MainWindow(ctx, ctx.theme)
    qtbot.addWidget(win)
    win.show_error(card)
    win._error_dialogs[-1].card.actionTriggered.emit(update)       # karttaki "Bileşenleri güncelle"
    assert len(calls) == 1 and calls[0] is ctx.service("settings")


def test_source_labels_say_where_each_component_comes_from(svc):
    assert S.source_label("bundled") == "Pakette"
    assert S.source_label("updated") == "Güncellendi"
    assert S.source_label("system") == S.source_label("env") == S.source_label("module") == "Sistemde"
    assert S.source_label("") == "Yok"


def test_tool_info_carries_the_source_of_each_component(svc):
    info = S.ToolInfo("2025.1", "7.1", "/opt/ffmpeg", "2.0", {"yt-dlp": "updated", "ffmpeg": "bundled", "deno": ""})
    assert info.sources == {"yt-dlp": "updated", "ffmpeg": "bundled", "deno": ""}
    assert S.ToolInfo("", "", "").sources == {}


def test_auto_update_setting_defaults_on_and_persists(svc):
    assert svc.auto_update() is True
    svc.set_auto_update(False)
    assert svc.auto_update() is False


# -- klasör ve önbellek --------------------------------------------------------
def test_project_root_must_be_a_folder(svc, tmp_path):
    with pytest.raises(ValueError):
        svc.set_project_root(str(tmp_path / "yok"))
    svc.set_project_root(str(tmp_path))
    assert svc.project_root() == str(tmp_path)
    info = svc.folder_info()
    assert info is not None and info.total > 0


def test_cache_target_is_last_project_when_none_open(svc, ctx, tmp_path):
    assert svc.cache_target() is None and svc.cache_bytes() == 0
    with pytest.raises(ValueError):
        svc.clean_cache()
    project = tmp_path / "Projem"
    (project / ".kadraj" / "cache").mkdir(parents=True)
    (project / ".kadraj" / "cache" / "thumb.bin").write_bytes(b"x" * 300)
    ctx.settings.set("last_project", str(project))
    assert svc.cache_target() == project
    assert svc.cache_bytes() == 300
    report = svc.clean_cache()
    assert report.removed == 1 and report.freed == 300
    assert svc.cache_bytes() == 0


def test_paths_info_names_every_location(svc, tmp_path):
    info = svc.paths_info()
    assert set(info) == {"project", "cache", "render", "temp", "comfyui"}


# -- sağlayıcı sırası ----------------------------------------------------------
def test_chain_order_is_saved_and_only_known_ids_kept(svc):
    ids = svc.chain_ids("llm")
    assert {"anthropic", "openai_compat", "ollama"} <= set(ids)
    svc.set_chain("llm", ["ollama", "anthropic", "uydurma"])
    assert svc.fallback_order()["llm"] == ["ollama", "anthropic"]
    assert svc.chain_ids("llm")[:2] == ["ollama", "anthropic"]


# -- görünüm -------------------------------------------------------------------
def test_accessibility_change_is_saved_and_broadcast(svc, ctx):
    seen = []
    unsubscribe = ctx.bus.subscribe(Topics.A11Y_CHANGED, seen.append)
    svc.set_a11y("large_text", True)
    assert svc.a11y("large_text") is True
    assert seen and seen[-1]["large_text"] is True
    unsubscribe()
    with pytest.raises(ValueError):
        svc.set_a11y("uydurma", True)


def test_ui_mode_round_trip(svc, ctx):
    svc.set_ui_mode("legend")
    assert ctx.ui_mode is UiMode.LEGEND and svc.ui_mode() == "legend"
    svc.set_ui_mode("simple")
    assert svc.ui_mode() == "simple"


def test_tips_toggle(svc):
    assert svc.tips_enabled() is True
    svc.set_tips(False)
    assert svc.tips_enabled() is False


# -- profiller -----------------------------------------------------------------
def test_profile_switch_applies_its_mode_and_accessibility(svc, ctx):
    created = svc.create_profile("Okul Projesi")
    assert created.id == "okul-projesi"
    profiles.update_profile(created.id, ui_mode="legend", a11y={"high_contrast": True})
    svc.switch_profile(created.id)
    assert ctx.ui_mode is UiMode.LEGEND and svc.a11y("high_contrast") is True
    svc.switch_profile("default")
    assert svc.a11y("high_contrast") is False
    svc.set_ui_mode("simple")
    svc.delete_profile(created.id)
    assert "okul-projesi" not in [p.id for p in svc.profile_list()]


def test_default_profile_cannot_be_deleted(svc):
    with pytest.raises(ProfileError) as exc:
        svc.delete_profile("default")
    assert exc.value.code == "default_locked"
