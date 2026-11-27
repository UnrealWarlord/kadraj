"""``kadraj/device.py`` ve Android'e özgü yollar: platform algılama, telefon/tablet ayrımı, özel klasör, kütüphane klasörü.

Gerçek cihaz gerekmez: Android ortamı ``KADRAJ_PLATFORM`` / ``KADRAJ_ANDROID_*`` değişkenleriyle ve sahte
``/proc/self/maps`` metniyle taklit edilir.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

from kadraj import device, paths
from kadraj.core import components as C
from kadraj.core import updater as U

# Android yalnız POSIX: HOME/Path.home(), "/storage/..." yolları ve "#!/bin/sh" betikleri Windows'ta anlamsız
_POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="Android'e özgü POSIX davranışı")

_ANDROID_VARS = ("KADRAJ_PLATFORM", "KADRAJ_FORM_FACTOR", "KADRAJ_ANDROID_PRIVATE", "KADRAJ_ANDROID_LIBDIR",
                 "ANDROID_ARGUMENT", "ANDROID_PRIVATE")


def _clear(monkeypatch, var):
    """Değişkeni sil; test bitince eski hâline döner (sınanan kod ``os.environ``'a kendisi yazsa bile)."""
    monkeypatch.setenv(var, "")
    monkeypatch.delenv(var)


@pytest.fixture()
def desktop(monkeypatch):
    for var in _ANDROID_VARS + ("TMPDIR", "SSL_CERT_FILE"):
        _clear(monkeypatch, var)
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)   # prepare_android_environment önbelleği sıfırlar
    monkeypatch.setattr(sys, "platform", "linux")
    if hasattr(sys, "getandroidapilevel"):
        monkeypatch.delattr(sys, "getandroidapilevel")


@pytest.fixture()
def android(desktop, monkeypatch, tmp_path):
    private = tmp_path / "data" / "user" / "0" / "org.insibag.kadraj" / "files"
    libdir = tmp_path / "data" / "app" / "org.insibag.kadraj-1" / "lib" / "arm64"
    private.mkdir(parents=True)
    libdir.mkdir(parents=True)
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    monkeypatch.setenv("ANDROID_PRIVATE", str(private))
    monkeypatch.setenv("KADRAJ_ANDROID_LIBDIR", str(libdir))
    return private, libdir


def test_desktop_is_not_android_and_form_factor_is_desktop(desktop):
    assert not device.is_android()
    assert device.form_factor() == device.DESKTOP
    assert device.android_private_dir() is None and device.native_library_dir() is None
    assert device.android_api_level() == 0


def test_android_detection_from_python_build_and_p4a_env(desktop, monkeypatch):
    monkeypatch.setattr(sys, "platform", "android")
    assert device.is_android()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("ANDROID_ARGUMENT", "/data/user/0/x/files")
    assert device.is_android()
    monkeypatch.setenv("KADRAJ_PLATFORM", "desktop")          # açık taklit her şeyi ezer
    assert not device.is_android()


def test_api_level_reads_sys_getandroidapilevel(desktop, monkeypatch):
    monkeypatch.setattr(sys, "getandroidapilevel", lambda: 34, raising=False)
    assert device.is_android() and device.android_api_level() == 34


@pytest.mark.parametrize("dp, expected", [(360, "phone"), (411.4, "phone"), (599.9, "phone"), (600, "tablet"),
                                          (800, "tablet"), (None, "phone"), (0, "phone")])
def test_sw600dp_threshold(dp, expected):
    assert device.classify(dp) == expected


def test_form_factor_on_android_uses_screen_and_can_be_forced(android, monkeypatch):
    assert device.form_factor(412) == device.PHONE
    assert device.form_factor(800) == device.TABLET
    monkeypatch.setenv("KADRAJ_FORM_FACTOR", "tablet")
    assert device.form_factor(412) == device.TABLET and device.is_tablet() and not device.is_phone()


def test_libdir_from_maps_skips_libraries_inside_the_apk():
    inside_apk = "7f00-7f10 r-xp 0 fd:01 1 /data/app/~~a/org.x-b/base.apk!/lib/arm64-v8a/libpython3.14.so\n"
    extracted = "7f20-7f30 r-xp 0 fd:01 2 /data/app/~~a/org.x-b/lib/arm64/libpython3.14.so\n"
    other = "7f40-7f50 r-xp 0 fd:01 3 /system/lib64/libc.so\n"
    assert device.libdir_from_maps(inside_apk + other) is None
    assert device.libdir_from_maps(other + inside_apk + extracted) == Path("/data/app/~~a/org.x-b/lib/arm64")


def test_home_dir_on_android_lives_in_private_storage(android, monkeypatch):
    private, _ = android
    monkeypatch.delenv("KADRAJ_HOME", raising=False)
    assert paths.home_dir() == private / ".kadraj"
    monkeypatch.setenv("KADRAJ_HOME", "/tmp/override")          # testler ve geliştirici yine ezebilir
    assert paths.home_dir() == Path("/tmp/override")


@_POSIX_ONLY
def test_prepare_android_environment_sets_home_to_documents_and_tmp_to_private(android, monkeypatch, tmp_path):
    private, _ = android
    docs = tmp_path / "storage" / "Android" / "data" / "org.insibag.kadraj" / "files" / "Documents"
    monkeypatch.setenv("HOME", "/")
    assert paths.prepare_android_environment(str(docs)) == docs
    assert os.environ["HOME"] == str(docs) and Path.home() == docs and paths.user_dir() == docs
    assert os.environ["TMPDIR"] == str(private / "tmp") and (private / "tmp").is_dir()
    import certifi
    assert os.environ["SSL_CERT_FILE"] == certifi.where()          # Android OpenSSL'inin CA klasörü yok


def test_prepare_android_environment_falls_back_to_private_dir(android, monkeypatch):
    private, _ = android
    monkeypatch.setenv("HOME", "/")
    assert paths.prepare_android_environment(None) == private
    assert os.environ["HOME"] == str(private)


@_POSIX_ONLY
def test_prepare_android_environment_keeps_home_dir_stable_when_home_moves(android, monkeypatch, tmp_path):
    private, _ = android
    monkeypatch.delenv("ANDROID_PRIVATE")                       # özel klasör yalnız (yazılabilir) HOME'dan bilinsin
    monkeypatch.delenv("KADRAJ_HOME", raising=False)
    monkeypatch.setenv("HOME", str(private))
    assert paths.home_dir() == private / ".kadraj"
    docs = tmp_path / "Documents"
    assert paths.prepare_android_environment(str(docs)) == docs
    assert Path.home() == docs and paths.home_dir() == private / ".kadraj"      # ayarlar/kasa yerinden oynamaz


def test_prepare_android_environment_resets_a_cached_tempdir(android, monkeypatch, tmp_path):
    private, _ = android
    monkeypatch.setenv("HOME", "/")
    tempfile.tempdir = str(tmp_path / "eski")                  # açılışta önceden hesaplanmış gibi
    paths.prepare_android_environment(str(tmp_path / "Documents"))
    assert tempfile.gettempdir() == str(private / "tmp")


def test_prepare_android_environment_does_nothing_on_desktop(desktop, monkeypatch):
    monkeypatch.setenv("HOME", "/home/someone")
    assert paths.prepare_android_environment("/tmp/x") is None
    assert os.environ["HOME"] == "/home/someone"


@pytest.fixture()
def tools(android, monkeypatch, tmp_path):
    _, libdir = android
    home = tmp_path / "home"
    (home / "tools").mkdir(parents=True)
    monkeypatch.setenv("KADRAJ_HOME", str(home))
    monkeypatch.delenv("KADRAJ_BUNDLED_TOOLS", raising=False)
    for var in ("KADRAJ_FFMPEG", "KADRAJ_FFPROBE", "KADRAJ_DENO", "KADRAJ_YTDLP", "KADRAJ_QUICKJS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(C.shutil, "which", lambda n: f"/system/bin/{n}")
    return home / "tools", libdir


def _exe(path):
    path.write_bytes(b"\x7fELF")
    os.chmod(path, 0o755)
    return path


def test_android_ffmpeg_comes_only_from_the_native_library_dir(tools):
    updated, libdir = tools
    _exe(updated / "ffmpeg")                                # W^X: yazılabilir klasördeki kopya çalıştırılamaz
    assert C.find("ffmpeg") is None                         # PATH de denenmez
    _exe(libdir / "libffmpeg.so")
    _exe(libdir / "libffprobe.so")
    hit = C.find("ffmpeg")
    assert hit.source == C.SOURCE_ANDROID and hit.path == str(libdir / "libffmpeg.so")
    assert C.ffprobe_path() == str(libdir / "libffprobe.so")
    assert C.ffmpeg_location() == str(C.android_links_dir())     # yt-dlp "ffmpeg"/"ffprobe" adlarını arar
    assert C.find("deno") is None


def test_android_env_override_still_wins(tools, monkeypatch, tmp_path):
    _, libdir = tools
    _exe(libdir / "libffmpeg.so")
    own = _exe(tmp_path / "my-ffmpeg")
    monkeypatch.setenv("KADRAJ_FFMPEG", str(own))
    assert C.find("ffmpeg").source == C.SOURCE_ENV


def test_android_updated_ytdlp_zipapp_is_still_used(tools, monkeypatch):
    updated, _ = tools
    (updated / "yt-dlp").write_bytes(b"PK")
    (updated / "versions.json").write_text('{"yt-dlp": {"version": "2026.11.23"}}', encoding="utf-8")
    monkeypatch.setattr(C, "module_ytdlp_version", lambda: "2026.01.01")
    choice = C.ytdlp_choice()
    assert choice.source == C.SOURCE_UPDATED and choice.version == "2026.11.23"


def test_android_updater_checks_only_ytdlp(tools, monkeypatch):
    asked = []

    def get_json(url, timeout=0):
        asked.append(url)
        raise OSError("çevrimdışı")

    u = U.Updater(get_json=get_json, get_text=lambda *a, **k: "", download=lambda *a, **k: "", platform=None)
    rep = u.run(force=True, big=True)
    assert not rep.skipped                                   # Android'de platform tablosu yok ama yt-dlp denetlenir
    assert asked == [U.YTDLP_API]                             # Deno ve FFmpeg sorulmaz


def test_android_settings_notes_and_source_label(android):
    from kadraj.core import i18n
    from kadraj.modules.settings import service as S
    from kadraj.modules.settings.strings import K
    assert S.update_note_key() == K.ANDROID_TOOLS_NOTE and S.repair_hint_key() == K.ANDROID_REPAIR_HINT
    assert S.source_label(C.SOURCE_ANDROID) == i18n.t(K.SOURCE_ANDROID)


def test_desktop_settings_notes_are_unchanged(desktop):
    from kadraj.modules.settings import service as S
    from kadraj.modules.settings.strings import K
    assert S.update_note_key() == K.UPDATE_STARTUP_NOTE and S.repair_hint_key() == K.REPAIR_HINT


def test_android_has_no_desktop_platform_table(android):
    assert U._platform() is None


def test_desktop_without_supported_platform_still_skips(desktop, monkeypatch, tmp_path):
    monkeypatch.setenv("KADRAJ_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(U, "_platform", lambda: None)          # ör. macOS ya da 32-bit Linux
    u = U.Updater(get_json=lambda *a, **k: pytest.fail("ağa çıkmamalı"), get_text=lambda *a, **k: "",
                  download=lambda *a, **k: "", platform=None)
    assert u.run(force=True).skipped


def test_android_quickjs_is_the_js_runtime(tools):
    _, libdir = tools
    assert C.js_runtime() is None
    qjs = _exe(libdir / "libqjs.so")
    assert C.find("deno") is None                                    # Deno'nun Android sürümü yok
    assert C.js_runtime() == (C.QUICKJS, str(qjs)) and C.js_runtime_label() == "QuickJS"


def test_desktop_js_runtime_prefers_deno_and_quickjs_needs_env(desktop, monkeypatch, tmp_path):
    monkeypatch.setenv("KADRAJ_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("KADRAJ_BUNDLED_TOOLS", raising=False)
    for var in ("KADRAJ_DENO", "KADRAJ_QUICKJS"):
        _clear(monkeypatch, var)
    monkeypatch.setattr(C.shutil, "which", lambda n: None)
    assert C.js_runtime() is None and C.js_runtime_label() == "Deno"
    qjs = _exe(tmp_path / "qjs")
    monkeypatch.setenv("KADRAJ_QUICKJS", str(qjs))
    assert C.js_runtime() == (C.QUICKJS, str(qjs))
    deno = _exe(tmp_path / "deno")
    monkeypatch.setenv("KADRAJ_DENO", str(deno))
    assert C.js_runtime() == ("deno", str(deno))
    assert "quickjs" not in C.NAMES                                  # paketleme/güncelleme birimi değil


def test_android_ffmpeg_location_is_a_folder_of_named_links(tools, monkeypatch, tmp_path):
    _, libdir = tools
    ff, fp = _exe(libdir / "libffmpeg.so"), _exe(libdir / "libffprobe.so")
    loc = Path(C.ffmpeg_location())
    assert loc == C.android_links_dir()
    assert (loc / "ffmpeg").is_symlink() and (loc / "ffmpeg").resolve() == ff.resolve()
    assert (loc / "ffprobe").resolve() == fp.resolve()
    newlib = tmp_path / "data" / "app" / "org.insibag.kadraj-2" / "lib" / "arm64"       # uygulama güncellendi
    newlib.mkdir(parents=True)
    ff2 = _exe(newlib / "libffmpeg.so")
    _exe(newlib / "libffprobe.so")
    monkeypatch.setenv("KADRAJ_ANDROID_LIBDIR", str(newlib))
    assert Path(C.ffmpeg_location()) == loc and (loc / "ffmpeg").resolve() == ff2.resolve()


@_POSIX_ONLY
def test_quickjs_version_comes_from_help_output(tools):
    _, libdir = tools
    qjs = libdir / "libqjs.so"
    qjs.write_text('#!/bin/sh\necho "QuickJS-ng version 0.18.0"\necho "usage: qjs [options] [file [args]]"\nexit 1\n')
    os.chmod(qjs, 0o755)
    assert C.probe_version(C.QUICKJS) == "0.18.0"


def test_android_downloader_uses_quickjs_and_the_link_folder(tools):
    from kadraj.modules.downloader import service as DS
    _, libdir = tools
    _exe(libdir / "libffmpeg.so")
    _exe(libdir / "libffprobe.so")
    qjs = _exe(libdir / "libqjs.so")
    opts = DS.engine_options()
    assert opts["js_runtimes"] == {"quickjs": {"path": str(qjs)}}
    assert opts["ffmpeg_location"] == str(C.android_links_dir())


def test_android_startup_line_names_quickjs(tools):
    from kadraj.ui.shell import startup
    _, libdir = tools
    _exe(libdir / "libqjs.so")
    js = startup.detect_tools()["deno"]
    assert js.label == "QuickJS" and js.ok


def test_android_subtitles_and_text_use_the_app_fonts(android):
    from kadraj.modules.editor import automations as au
    from kadraj.modules.editor import filtergraph as FG
    fonts = paths.fonts_dir()
    assert (fonts / FG.ANDROID_FONT_FILE).is_file()
    extra, style = FG.android_subtitle_args("FontSize=24", srt=True)
    assert extra == f":fontsdir={FG.quote(str(fonts))}" and style == "FontName=Inter,FontSize=24"
    assert FG.android_subtitle_args("FontName=Nunito,FontSize=24", srt=True)[1] == "FontName=Nunito,FontSize=24"
    assert FG.android_subtitle_args("", srt=False) == (extra, "")           # .ass'ın kendi stili var
    assert FG.default_text_font() == str(fonts / FG.ANDROID_FONT_FILE)
    assert "Style: Dyn,Inter," in au.build_ass([], 160, 90)


def test_desktop_subtitles_and_text_are_unchanged(desktop):
    from kadraj.modules.editor import automations as au
    from kadraj.modules.editor import filtergraph as FG
    assert FG.android_subtitle_args("FontSize=24", srt=True) == ("", "FontSize=24")
    assert FG.default_text_font() == "" and "Style: Dyn,Sans," in au.build_ass([], 160, 90)


def test_android_settings_name_quickjs_for_the_js_row(android):
    from kadraj.modules.settings import service as S
    from kadraj.modules.settings.strings import K
    assert S.js_tool_key() == K.TOOL_QUICKJS
