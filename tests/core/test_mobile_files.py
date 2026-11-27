"""Mobil 6. adım (M20): Android köprüsü, galeriye kaydet, dosya eylemleri, telefondan dosya seçme, geri tuşu.

Java tarafı (``packaging/android/java``) burada çalışmaz: köprünün ``ready`` dosyası ve sonuç dosyaları testte elle
yazılır. Android ``KADRAJ_PLATFORM=android`` ile taklit edilir; ortak depolama ``KADRAJ_SHARED_STORAGE`` ile geçici bir
klasördür. Gerçek bir oynatıcı ya da paylaşım menüsü açılmaz.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QFile, QIODevice, Qt, QThreadPool
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QAbstractButton, QApplication, QFileDialog

from kadraj import bridge, device
from kadraj.branding import APP_NAME
from kadraj.core import i18n
from kadraj.core.contracts import JobStatus, PROJECT_FILE_NAME
from kadraj.storage import gallery
from kadraj.ui import files

ROOT = Path(__file__).resolve().parents[2]
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                    "0000000d4944415478da63f8ffff3f0005fe02fea7d6a4a50000000049454e44ae426082")


@pytest.fixture()
def android(monkeypatch, clean_home, tmp_path):
    """Android taklidi: köprü bu süreçte "çalışıyor", ortak depolama geçici klasör, Android 14."""
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    monkeypatch.setenv("KADRAJ_FORM_FACTOR", "phone")
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("KADRAJ_SHARED_STORAGE", str(shared))
    monkeypatch.setattr(device, "android_api_level", lambda: 34)
    folder = bridge.bridge_dir()
    folder.mkdir(parents=True)
    (folder / bridge.READY).write_text(json.dumps({"protocol": 1, "pid": os.getpid()}), encoding="utf-8")
    return shared


def _requests():
    out = []
    for f in sorted(bridge.bridge_dir().glob("*.req")):
        out.append(json.loads(f.read_text(encoding="utf-8")))
    return out


def _answer(req_id: str, **result) -> None:
    (bridge.bridge_dir() / f"{req_id}.res").write_text(json.dumps(result), encoding="utf-8")


# ------------------------------------------------------------------------------------------------ köprü
def test_bridge_is_off_on_desktop(clean_home):
    assert not bridge.available()
    assert bridge.send("open", path="/x") is None and bridge.share("/x") is None


def test_bridge_needs_a_ready_file_from_this_process(monkeypatch, clean_home):
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    assert not bridge.available()                                       # Java tarafı başlamadı
    folder = bridge.bridge_dir()
    folder.mkdir(parents=True)
    (folder / bridge.READY).write_text(json.dumps({"protocol": 1, "pid": os.getpid() + 1}), encoding="utf-8")
    assert not bridge.available()                                       # önceki oturumdan kalan dosya
    (folder / bridge.READY).write_text("bozuk", encoding="utf-8")
    assert not bridge.available()
    (folder / bridge.READY).write_text(json.dumps({"protocol": 1, "pid": os.getpid()}), encoding="utf-8")
    assert bridge.available()


def test_bridge_writes_whole_requests_and_reads_results_once(android, tmp_path):
    clip = tmp_path / "klip.mp4"
    req = bridge.share(clip, mime="video/mp4", title="Paylaş")
    assert req and _requests() == [{"id": req, "action": "share", "path": str(clip), "mime": "video/mp4",
                                    "title": "Paylaş"}]
    assert not list(bridge.bridge_dir().glob("*.tmp"))                 # yarım dosya kalmaz
    assert bridge.take_result(req) is None                              # henüz yanıt yok
    _answer(req, ok=False, error="no_app")
    assert bridge.take_result(req) == {"ok": False, "error": "no_app"}
    assert bridge.take_result(req) is None                              # okunan sonuç silinir
    (bridge.bridge_dir() / "x.res").write_text("{bozuk", encoding="utf-8")
    assert bridge.take_result("x") == {"ok": False, "error": "failed"}


def test_screen_keeper_sends_only_changes_and_retries_when_the_bridge_was_missing():
    sent, alive = [], {"on": False}

    def sender(on):
        if not alive["on"]:
            return None
        sent.append(on)
        return "id"

    keeper = bridge.ScreenKeeper(sender)
    keeper.set("recorder", True)
    assert sent == [] and keeper.on                                     # köprü yok: gönderilemedi
    alive["on"] = True
    keeper.set("jobs", True)
    assert sent == [True]                                               # köprü geldi: durum gönderildi
    keeper.set("recorder", False)
    assert sent == [True] and keeper.reasons() == {"jobs"}              # hâlâ bir neden var: değişiklik yok
    keeper.set("jobs", False)
    assert sent == [True, False] and not keeper.on


# ------------------------------------------------------------------------------------------------ galeri
@pytest.mark.parametrize("name, folder", [("a.mp4", "Movies"), ("a.MOV", "Movies"), ("ses.wav", "Music"),
                                          ("g.png", "Pictures"), ("g.jpeg", "Pictures"), ("s.pdf", "Download"),
                                          ("s.fountain", "Download")])
def test_gallery_folder_follows_the_media_type(name, folder):
    assert gallery.folder_for(name) == f"{folder}/{APP_NAME}"


def test_gallery_copies_without_overwriting(android, tmp_path):
    src = tmp_path / "Klip.mp4"
    src.write_bytes(b"video")
    first = gallery.save(src)
    second = gallery.save(src)
    assert first == android / "Movies" / APP_NAME / "Klip.mp4" and first.read_bytes() == b"video"
    assert second.name == "Klip (2).mp4" and src.exists()


def test_gallery_errors_have_codes(android, monkeypatch, tmp_path):
    with pytest.raises(gallery.GalleryError) as missing:
        gallery.save(tmp_path / "yok.mp4")
    assert missing.value.code == "not_found"
    src = tmp_path / "Klip.mp4"
    src.write_bytes(b"video")

    def denied(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(gallery.shutil, "copyfile", denied)
    with pytest.raises(gallery.GalleryError) as err:
        gallery.save(src)
    assert err.value.code == "denied"
    monkeypatch.setattr(device, "android_api_level", lambda: 29)       # Android 10: ortak klasöre yazma izin ister
    assert not gallery.available()
    with pytest.raises(gallery.GalleryError) as old:
        gallery.save(src)
    assert old.value.code == "old_android"
    assert not list((android / "Movies" / APP_NAME).glob("*"))          # yarım kopya kalmaz


def test_gallery_root_comes_from_the_app_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("KADRAJ_SHARED_STORAGE", raising=False)
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    monkeypatch.setenv("HOME", "/storage/emulated/0/Android/data/org.insibag.kadraj/files/Documents")
    assert gallery.shared_root() == Path("/storage/emulated/0")
    monkeypatch.setenv("KADRAJ_PLATFORM", "desktop")
    assert gallery.shared_root() is None and not gallery.available()


# ------------------------------------------------------------------------------------------------ dosya eylemleri
def test_desktop_keeps_reveal_and_adds_no_mobile_buttons(qapp):
    assert files.can_reveal() and not files.can_share() and not files.can_save_to_gallery()
    assert files.mobile_buttons(lambda: "/x", lambda _t: None) == []


def test_android_buttons_share_through_the_bridge_and_report_errors(android, qtbot, tmp_path):
    clip = tmp_path / "Klip.mp4"
    clip.write_bytes(b"video")
    notes = []
    buttons = files.mobile_buttons(lambda: str(clip), notes.append, open_button=True)
    assert [b.text() for b in buttons] == [i18n.t("files.open"), i18n.t("files.share"), i18n.t("files.gallery")]
    assert not files.can_reveal() and files.can_share()
    buttons[1].click()
    (req,) = _requests()
    assert req["action"] == "share" and req["path"] == str(clip)
    _answer(req["id"], ok=False, error="no_app")
    qtbot.waitUntil(lambda: notes == [i18n.t("files.err.no_app")], timeout=3000)
    for b in buttons:
        b.deleteLater()


def test_android_open_says_why_when_it_cannot(android, qtbot, tmp_path):
    notes = []
    assert not files.open_path(tmp_path / "yok.mp4", notes.append)
    assert notes == [i18n.t("files.err.not_found")]
    clip = tmp_path / "Klip.mp4"
    clip.write_bytes(b"video")
    (bridge.bridge_dir() / bridge.READY).unlink()                       # Java tarafı yok (eski APK)
    assert not files.open_path(clip, notes.append)
    assert notes[-1] == i18n.t("files.err.unavailable")


def test_android_open_succeeds_quietly(android, qtbot, tmp_path, monkeypatch):
    clip = tmp_path / "Klip.mp4"
    clip.write_bytes(b"video")
    notes = []
    assert files.open_path(clip, notes.append)
    (req,) = _requests()
    assert req["action"] == "open"
    _answer(req["id"], ok=True)
    qtbot.waitUntil(lambda: not (bridge.bridge_dir() / f"{req['id']}.res").exists(), timeout=3000)
    assert notes == []


def test_save_to_gallery_runs_in_the_background_and_asks_for_a_scan(android, qtbot, tmp_path):
    clip = tmp_path / "Klip.mp4"
    clip.write_bytes(b"video")
    notes, done = [], []
    assert files.save_to_gallery(clip, notes.append, done.append)
    QThreadPool.globalInstance().waitForDone(5000)
    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    assert done == [android / "Movies" / APP_NAME / "Klip.mp4"]
    assert notes == [i18n.t("files.gallery.running"), i18n.t("files.gallery.done", folder=f"Movies/{APP_NAME}")]
    assert [r["action"] for r in _requests()] == ["scan"]


# ------------------------------------------------------------------------------------------------ dosya seçme
class _Source:
    """``content://`` adresini yerel bir dosyayla taklit eder (Qt'nin Android içerik motoru yerine)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __call__(self, _uri: str) -> QFile:
        f = QFile(str(self.path))
        assert f.open(QIODevice.OpenModeFlag.ReadOnly)
        return f


def test_content_uri_is_copied_once_and_gets_an_extension(android, monkeypatch, tmp_path):
    src = tmp_path / "kaynak"
    src.write_bytes(PNG)
    monkeypatch.setattr(files, "_open_source", _Source(src))
    uri = "content://com.android.providers.media.documents/document/image%3A42"
    first = files.localize(uri)
    assert first and Path(first).parent == files.import_dir() and Path(first).suffix == ".png"
    assert Path(first).read_bytes() == PNG
    assert files.localize(uri) == first                                 # aynı dosya yeniden kopyalanmaz
    assert files.localize("/yerel/dosya.mp4") == "/yerel/dosya.mp4"


def test_unreadable_content_uri_gives_a_plain_note(android, monkeypatch):
    def broken(_uri):
        raise OSError("izin yok")

    monkeypatch.setattr(files, "_open_source", broken)
    notes = []
    assert files.localize("content://x/y", note=notes.append) == ""
    assert notes == [i18n.t("files.import.failed")]


def test_pickers_return_local_paths(android, monkeypatch, tmp_path):
    src = tmp_path / "kaynak.png"
    src.write_bytes(PNG)
    monkeypatch.setattr(files, "_open_source", _Source(src))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("content://p/d/foto.png", "")))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
                        staticmethod(lambda *a, **k: (["content://p/d/a.png", "/yerel/b.mp4"], "")))
    one = files.pick_file(None, "Seç")
    assert Path(one).name == "foto.png" and Path(one).is_file()
    many = files.pick_files(None, "Seç")
    assert Path(many[0]).name == "a.png" and many[1] == "/yerel/b.mp4"


# ------------------------------------------------------------------------------------------------ sayfalar
def test_download_row_offers_share_and_gallery_instead_of_folder_on_android(android, qtbot, tmp_path):
    from kadraj.modules.downloader import strings as dl_strings
    from kadraj.modules.downloader.rows import RecentDownloadRow
    i18n.register_strings(dl_strings.STRINGS)
    clip = tmp_path / "Klip.mp4"
    clip.write_bytes(b"x")
    row = RecentDownloadRow(None, {"id": 1, "title": "Video", "status": JobStatus.DONE, "progress": 1.0,
                                   "path": str(clip), "error": ""})
    qtbot.addWidget(row)
    texts = [b.text() for b in row.findChildren(QAbstractButton)]
    assert i18n.t(dl_strings.K.BTN_SHOW_FOLDER) not in texts
    assert i18n.t("files.share") in texts and i18n.t("files.gallery") in texts
    assert i18n.t(dl_strings.K.BTN_PLAY) in texts


def test_recorder_cards_hide_folder_actions_on_android(android, qtbot):
    from kadraj.modules.recorder import strings as rec_strings
    from kadraj.modules.recorder.location import RecordingLocationBar, RecordingResultCard
    i18n.register_strings(rec_strings.STRINGS)
    bar = RecordingLocationBar()
    card = RecordingResultCard()
    qtbot.addWidget(bar)
    qtbot.addWidget(card)
    assert bar.open_btn.isHidden() and bar.change_btn.isHidden()
    assert bar.hint.text() == i18n.t("files.android_folder")
    assert card.show_btn.isHidden() and [b.objectName() for b in card.mobile_btns] == ["file_share", "file_gallery"]


def test_project_picker_lists_project_folders_newest_first(ctx, qtbot, tmp_path, monkeypatch):
    from kadraj.ui.shell.main_window import MainWindow
    root = tmp_path / "projeler"
    for i, name in enumerate(("Eski", "Yeni")):
        (root / name).mkdir(parents=True)
        f = root / name / PROJECT_FILE_NAME
        f.write_text("{}", encoding="utf-8")
        os.utime(f, (1000 + i, 1000 + i))
    (root / "Klasör").mkdir()                                           # proje değil
    win = MainWindow(ctx, ctx.theme)
    qtbot.addWidget(win)
    assert [p.name for p in win.project_folders(root)] == ["Yeni", "Eski"]
    assert win.project_folders(tmp_path / "yok") == []


def test_back_key_goes_home_then_needs_a_second_press_to_exit(ctx, qtbot, monkeypatch):
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    monkeypatch.setenv("KADRAJ_FORM_FACTOR", "phone")
    from kadraj.ui.shell.main_window import MainWindow
    win = MainWindow(ctx, ctx.theme)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    win.open_module("settings")
    assert ctx.current_module == "settings"

    def back() -> bool:
        press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Back, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(win.windowHandle(), press)
        release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Back, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(win.windowHandle(), release)
        return press.isAccepted() and release.isAccepted()

    assert back() and ctx.current_module is None                        # sayfadan ana ekrana
    assert back()                                                        # ana ekranda ilk basış: ipucu
    assert win.handle_back() is False                                    # ikinci basış: Qt pencereyi kapatır


def test_jobs_keep_the_screen_on(ctx, qtbot, monkeypatch):
    from kadraj.ui.shell.main_window import MainWindow
    calls = []
    monkeypatch.setattr(bridge.screen, "set", lambda reason, on: calls.append((reason, on)))
    win = MainWindow(ctx, ctx.theme)
    qtbot.addWidget(win)
    win._sync_jobs()
    assert calls[-1] == ("jobs", False)


# ------------------------------------------------------------------------------------------------ APK parçaları
def _hook():
    spec = importlib.util.spec_from_file_location("p4a_hook", ROOT / "packaging" / "android" / "p4a_hook.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ACTIVITY = """public class PythonActivity extends QtActivity {
    long lastBackClick = SystemClock.elapsedRealtime();

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (SystemClock.elapsedRealtime() - lastBackClick > 2000) {
            lastBackClick = SystemClock.elapsedRealtime();
            Toast.makeText(this, "Click again to close the app", Toast.LENGTH_LONG).show();
            return true;
        }

        lastBackClick = SystemClock.elapsedRealtime();
        return super.onKeyDown(keyCode, event);
    }

    public interface NewIntentListener {
"""


def test_p4a_hook_adds_the_provider_and_frees_the_back_key():
    hook = _hook()
    manifest = "<manifest><application android:name=\"x\">\n<activity/>\n</application></manifest>"
    once = hook.patch_manifest(manifest)
    assert 'android:name="org.insibag.kadraj.KadrajFiles"' in once and 'android:exported="false"' in once
    assert once.index("KadrajFiles") < once.index("</application>") and hook.patch_manifest(once) == once
    patched = hook.patch_activity(ACTIVITY)
    assert "onKeyDown" not in patched and "Click again" not in patched and "NewIntentListener" in patched
    assert hook.patch_activity(patched) == patched
    with pytest.raises(SystemExit):
        hook.patch_activity("public class PythonActivity {}")         # p4a değiştiyse derleme durur


def test_java_bridge_and_python_agree_on_the_protocol():
    java = (ROOT / "packaging" / "android" / "java" / "org" / "insibag" / "kadraj" / "Bridge.java").read_text(encoding="utf-8")
    assert f"PROTOCOL = {bridge.PROTOCOL};" in java
    assert '".kadraj"' in java and '"bridge"' in java and bridge.bridge_dir().name == "bridge"
    for action in ("open", "share", "screen", "scan"):
        assert f'"{action}"' in java
    for code in ("not_found", "not_shared", "no_app", "denied", "bad_request", "unknown_action", "failed"):
        assert f'"{code}"' in java and code in bridge.ERRORS
    for code in bridge.ERRORS:                                          # her hata kodunun sade bir cümlesi var
        assert files.bridge_error_text(code) and i18n.has("files.err.failed")
    build = (ROOT / "packaging" / "android" / "build_apk.sh").read_text(encoding="utf-8")
    assert '"android.add_src"' in build and '"p4a.hook"' in build
