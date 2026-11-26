"""``core/updater.py``: yalnızca HTTPS + izinli sunucular, SHA-256 doğrulaması, atomik yerleştirme, geri alma, çevrimdışı sessizlik."""
import hashlib
import io
import json
import urllib.error
import zipfile

import pytest

from kadraj.core import components as C
from kadraj.core import updater as U

PLAT = {"ff": "linux64", "ff_ext": ".tar.xz", "deno": "x86_64-unknown-linux-gnu"}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KADRAJ_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KADRAJ_BUNDLED_TOOLS", str(tmp_path / "bundled-yok"))
    monkeypatch.setattr(C, "module_ytdlp_version", lambda: "")
    monkeypatch.setattr(C.shutil, "which", lambda _n: None)
    return tmp_path / "home" / "tools"


def _zipapp_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("yt_dlp/__init__.py", "")
        z.writestr("__main__.py", "")
    return buf.getvalue()


class Net:
    """Sahte ağ: sürüm JSON'ları + indirme. ``tamper`` → sağlama uyuşmaz."""

    def __init__(self, version="2026.11.23", tamper=False, offline=False):
        self.body = _zipapp_bytes()
        digest = hashlib.sha256(self.body).hexdigest()
        self.version, self.offline = version, offline
        self.sums = f"{('0' * 64) if tamper else digest}  yt-dlp\n"
        self.downloads = []

    def get_json(self, url, timeout=0):
        if self.offline:
            raise urllib.error.URLError("yok")
        assert url.startswith("https://")
        return {"tag_name": self.version, "assets": [
            {"name": "yt-dlp", "browser_download_url": "https://github.com/yt-dlp/yt-dlp/releases/download/x/yt-dlp"},
            {"name": "SHA2-256SUMS", "browser_download_url": "https://github.com/yt-dlp/yt-dlp/releases/download/x/SHA2-256SUMS"}]}

    def get_text(self, url, timeout=0):
        return self.sums

    def download(self, url, dest, *, max_bytes, progress=None, timeout=0):
        self.downloads.append(url)
        dest.write_bytes(self.body)
        return hashlib.sha256(self.body).hexdigest()


def _updater(net, big=False):
    u = U.Updater(get_json=net.get_json, get_text=net.get_text, download=net.download, platform=PLAT)
    return u


def test_only_https_and_official_hosts_are_allowed():
    assert U.check_url("https://github.com/yt-dlp/yt-dlp/releases/download/x/yt-dlp")
    for bad in ("http://github.com/x", "https://evil.example.com/x", "ftp://github.com/x", "file:///etc/passwd"):
        with pytest.raises(U.UpdateError):
            U.check_url(bad)


def test_parse_sums_accepts_star_and_plain_forms():
    h1, h2 = "a" * 64, "b" * 64
    assert U.parse_sums(f"{h1}  yt-dlp\n{h2} *dir/deno.zip\nçöp\n") == {"yt-dlp": h1, "deno.zip": h2}


def test_ytdlp_update_installs_verifies_and_records_version(home):
    net = Net()
    rep = _updater(net).run(big=False, force=True)
    assert rep.updated == ["yt-dlp"] and not rep.errors
    assert (home / "yt-dlp").is_file()
    assert json.loads((home / C.VERSIONS_FILE).read_text(encoding="utf-8"))["yt-dlp"]["version"] == "2026.11.23"
    assert C.ytdlp_choice().source == C.SOURCE_UPDATED


def test_checksum_mismatch_is_rejected_and_nothing_is_installed(home):
    rep = _updater(Net(tamper=True)).run(big=False, force=True)
    assert not rep.updated and any("SHA-256" in e for e in rep.errors)
    assert not (home / "yt-dlp").exists()


def test_offline_is_silent(home):
    rep = _updater(Net(offline=True)).run(big=False, force=True)
    assert rep.offline and not rep.errors and not rep.updated


def test_same_version_is_not_downloaded_again_and_interval_is_respected(home):
    net = Net()
    _updater(net).run(big=False, force=True)
    assert len(net.downloads) == 1
    again = _updater(net).run(big=False, force=True)
    assert again.current == ["yt-dlp"] and len(net.downloads) == 1
    quiet = _updater(net).run(big=False)                  # 6 saat dolmadı → hiç sorgulamaz
    assert quiet.skipped and len(net.downloads) == 1


def test_swap_keeps_previous_and_rollback_restores(home):
    _updater(Net(version="2026.10.01")).run(big=False, force=True)
    _updater(Net(version="2026.11.23")).run(big=False, force=True)
    assert (home / ".prev" / "yt-dlp").is_file()
    assert U.rollback("yt-dlp") is True
    assert json.loads((home / C.VERSIONS_FILE).read_text(encoding="utf-8"))["yt-dlp"]["version"] == "2026.10.01"
    assert U.rollback("yt-dlp") is False                  # önceki kalmadı


def test_broken_download_is_not_swapped_in(home):
    net = Net()
    net.body = b"zip degil"
    net.sums = f"{hashlib.sha256(net.body).hexdigest()}  yt-dlp\n"
    rep = _updater(net).run(big=False, force=True)
    assert not rep.updated and rep.errors and not (home / "yt-dlp").exists()


def test_ffmpeg_release_picks_highest_branch_and_variant(monkeypatch):
    sums = {n: "c" * 64 for n in ("ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz", "ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz")}
    text = "\n".join(f"{h}  {n}" for n, h in sums.items())
    assets = [{"name": n, "browser_download_url": f"https://github.com/x/{n}"} for n in sums] + [
        {"name": "ffmpeg-master-latest-linux64-gpl.tar.xz", "browser_download_url": "https://github.com/x/m"},
        {"name": "ffmpeg-n9.0-latest-linux64-lgpl-9.0.tar.xz", "browser_download_url": "https://github.com/x/l"},
        {"name": "checksums.sha256", "browser_download_url": "https://github.com/x/checksums.sha256"}]
    rel = U.latest_ffmpeg(PLAT, lambda url, timeout=0: {"assets": assets}, lambda url, timeout=0: text)
    assert rel.version == "9.0" and rel.asset.endswith("gpl-9.0.tar.xz")


# -- seed (paketleme) -------------------------------------------------------------------------------------
def test_seed_downloads_ytdlp_even_when_the_pip_module_is_already_the_latest(home, monkeypatch):
    """CI'daki hata: derleme ortamındaki pip yt-dlp en yeni sürümdü → "güncel" sayılıp hiç indirilmedi."""
    monkeypatch.setattr(C, "module_ytdlp_version", lambda: "2026.11.23")
    net = Net(version="2026.11.23")
    normal = _updater(net).run(big=False, force=True)
    assert normal.current == ["yt-dlp"] and not net.downloads            # kullanıcı uygulamasında doğru davranış
    u = U.Updater(get_json=net.get_json, get_text=net.get_text, download=net.download, platform=PLAT, seed=True)
    rep = u.run(big=False, force=True)
    assert rep.updated == ["yt-dlp"] and len(net.downloads) == 1 and (home / "yt-dlp").is_file()


def test_failing_progress_callback_never_stops_an_update(home):
    """CI'daki hata: cp1252 konsola 'ı' yazan ilerleme çıktısı UnicodeEncodeError verip bütün güncellemeyi yutuyordu."""
    def bad(_text, _fraction=None):
        raise UnicodeEncodeError("charmap", "ı", 0, 1, "karakter eşlenemiyor")
    rep = _updater(Net()).run(bad, big=False, force=True)
    assert rep.updated == ["yt-dlp"] and not rep.errors


def _patched_updater(monkeypatch, net):
    real = U.Updater
    monkeypatch.setattr(U, "Updater", lambda seed=False: real(get_json=net.get_json, get_text=net.get_text,
                                                              download=net.download, platform=PLAT, seed=seed))


def test_seed_with_nothing_staged_reports_a_clear_error_not_a_traceback(home, tmp_path, monkeypatch, capsys):
    _patched_updater(monkeypatch, Net(offline=True))
    dest = tmp_path / "araclar"
    rep = U.seed(dest)                                      # hazırlık klasörü hiç oluşmaz
    assert rep.offline or rep.errors
    assert U.main(["--seed", str(dest)]) == 1
    err = capsys.readouterr().err
    assert "HATA" in err and "eksik" in err


def test_main_survives_a_cp1252_console(home, tmp_path, monkeypatch):
    import io
    import sys
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252", errors="strict"))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict"))

    def fake_seed(dest, progress=None):
        progress("yt-dlp sürümü sorgulanıyor…", None)       # 'ı' cp1252'de yok
        return U.Report(updated=["yt-dlp"])
    monkeypatch.setattr(U, "seed", fake_seed)
    assert U.main(["--seed", str(tmp_path / "x")]) == 1      # dosyalar yok → eksik; ama çökmeden
    sys.stdout.flush()
    assert "ı".encode("utf-8") in raw.getvalue()
