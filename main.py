"""KADRAJ giriş noktası.

    python main.py                  # splash → (ilk açılışta) sihirbaz → ana pencere
    python main.py --launcher       # splash'i atla, doğrudan ana pencere
    python main.py --mode legend    # belirli modda aç (simple | legend)
    python main.py --selftest       # ekransız kendi kendine sınama (ağ kapalı, geçici ev dizini)
    python main.py --selftest --screenshots DIR   # ayrıca iki modun ekran görüntülerini DIR'e yazar
    python main.py --selftest-download URL [DIR]  # GERÇEK indirme sınaması (AĞ AÇIK): en iyi ≤1080p + birleştirme + ffprobe
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional


def _arg_value(argv: List[str], name: str) -> Optional[str]:
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def _utf8_stdio() -> None:
    """Windows'ta yönlendirilmiş çıktı (CI günlüğü, boru) ANSI kod sayfasına düşer; Türkçe harfler hata verirdi.
    Konsolsuz (pencereli) paketlenmiş uygulamada ``sys.stdout`` yoktur (``None``) — o durumda atlanır."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def selftest(argv: Optional[List[str]] = None) -> int:
    """Çekirdeğin ayakta olduğunu kanıtlar. Gerçek ``~/.kadraj``'a dokunmaz, ağa çıkmaz."""
    argv = argv or []
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    tmp = tempfile.TemporaryDirectory(prefix="kadraj-selftest-", ignore_cleanup_errors=True)   # Windows: açık dosya silmeyi engeller
    os.environ["KADRAJ_HOME"] = str(Path(tmp.name) / "home")
    os.environ["KADRAJ_NO_UPDATE"] = "1"                  # selftest ağa çıkmaz: bileşen güncelleyici kapalı
    os.environ["HOME"] = tmp.name
    os.environ["USERPROFILE"] = tmp.name                  # Windows'ta Path.home() HOME'a değil USERPROFILE'a bakar
    results: List[tuple] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(f"[{'ok' if ok else 'HATA'}] {name}" + (f" — {detail}" if detail else ""))

    # ağ kapalı kanıtı: bağlanmaya çalışan her şey hata verir
    import socket
    attempts: List[str] = []

    def _blocked(self, *a, **k):          # noqa: ANN001
        # Yerel servis algılama (Ollama 11434, ComfyUI 8188) loopback'e bağlanır: bu internete çıkmak değildir;
        # yine de engellenir (çevrimdışı gibi davranılır) ama "ağa çıkma" sayılmaz.
        host = a[0][0] if a and isinstance(a[0], tuple) and a[0] else ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            attempts.append(str(a))
        raise OSError("selftest: ağ kapalı")

    socket.socket.connect = _blocked      # type: ignore[assignment]

    from kadraj import __version__
    from kadraj.storage import vault as vault_mod
    vault_mod.set_default_keyring(vault_mod.MemoryKeyring())      # selftest gerçek OS keyring'e dokunmaz
    from kadraj.core import i18n
    from kadraj.core.contracts import PROJECT_SUBFOLDERS, UiMode
    from kadraj.ui import fonts
    from kadraj.ui.shell.app import create_app
    from kadraj.ui.shell.main_window import MainWindow
    from kadraj.ui.widgets.lockup import Lockup, signature_px

    app, ctx, theme = create_app(["kadraj"])
    theme.set_animations(False)
    ctx.settings.set("wizard.done", True)
    ctx.set_language("tr")                          # dil açıkça ayarlanır: sonuç sistem yerel ayarına (LANG) bağlı olmasın
    check("sürüm", True, __version__)
    loaded = fonts.load_fonts()
    check("gömülü yazı tipleri", all(loaded.values()), ", ".join(f"{k}={'ok' if v else 'yok→sistem'}" for k, v in loaded.items()))
    check("kayıt defteri keşfi", ctx.registry.errors == [], f"{len(ctx.registry.specs())} modül kayıtlı, "
          f"{len(ctx.registry.entries())} kart")
    check("imza oranı (0.30, ≥9 px)", signature_px(44) == 13 and signature_px(20) == 9)
    check("i18n tr/en", i18n.t("home.greeting") != "home.greeting")

    root = ctx.create_project("Selftest Projesi", Path(tmp.name) / "projeler")
    ok = root is not None and all((root / f).is_dir() for f in PROJECT_SUBFOLDERS) and (root / "proje.kadraj").is_file()
    check("proje oluşturma (klasörler + dosya + db)", bool(ok) and (root / ".kadraj" / "proje.db").is_file())
    check("db şeması", set(ctx.project.db.tables()) >= {"media", "jobs", "snapshots", "prompts", "api_usage", "series_state"})

    _selftest_security(ctx, check, tmp.name, attempts)
    _selftest_recorder(ctx, check, tmp.name)
    _selftest_editor(ctx, check, tmp.name)
    _selftest_ai(ctx, check, tmp.name)

    win = MainWindow(ctx, theme)
    shots = _arg_value(argv, "--screenshots")
    for mode in (UiMode.SIMPLE, UiMode.LEGEND, UiMode.SIMPLE):
        ctx.set_ui_mode(mode)
        win.resize(1320, 860)
        win.show()
        app.processEvents()
        check(f"ana pencere {mode.value} modunda açıldı", win.isVisible() and ctx.project.is_open,
              f"proje korundu: {ctx.project.name}")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            win.grab().save(str(Path(shots) / f"{mode.value}.png"))
    check("mod geçişinde proje durumu korundu", ctx.project.is_open)
    check("ağa çıkma girişimi yok", not attempts,
          f"{len(attempts)} deneme" + (f": {attempts[:3]}" if attempts else ""))
    win.close()
    ctx.shutdown()
    failed = [n for n, ok_, _ in results if not ok_]
    print("-" * 62)
    print("SELFTEST " + ("BAŞARISIZ: " + ", ".join(failed) if failed else "TAMAM"))
    tmp.cleanup()
    return 1 if failed else 0


def _selftest_security(ctx, check, tmpdir: str, attempts: list) -> None:
    """Adım 3: kasa, sağlayıcı, otomatik kayıt/geri dönüş, snapshot, iş kuyruğu, sistem izleyici."""
    import json
    import time

    secret = "sk-selftest-SECRET-000111222"
    ctx.vault.set_secret("anthropic", secret)
    raw = ctx.vault.path.read_bytes()
    check("kasa: şifreli yazıldı, düz anahtar dosyada yok", secret.encode() not in raw and ctx.vault.has_secret("anthropic"),
          f"maskeli: {ctx.vault.masked('anthropic')}")
    from pathlib import Path
    from kadraj.storage import fernet_lite
    from kadraj.storage.vault import Vault
    phone = Vault(Path(tmpdir) / "telefon-kasa.bin", kdf_iterations=1000)        # anahtar zinciri olmayan cihaz (M19)
    phone.initialize("selftest-parola-42")
    phone.set_secret("pexels", secret)
    reopened = Vault(phone.path, kdf_iterations=1000)
    locked = reopened.status().state == "locked"
    reopened.unlock("selftest-parola-42")
    ref = Vault._crypto()[0]                                                    # masaüstünde cryptography'nin Fernet'i
    key = ref.generate_key()
    cross = fernet_lite.Fernet(key).decrypt(ref(key).encrypt(b"kadraj")) == b"kadraj"
    check("kasa (telefon): parola kipi, kilitle/aç, saf Python Fernet uyumlu",
          locked and reopened.get_secret("pexels") == secret and cross, f"Fernet: {ref.__module__}")
    mark = len(attempts)
    res = ctx.providers.test_connection("anthropic", ctx.vault, timeout=1.0)    # bilerek ağa çıkar; engellenir
    probed = len(attempts) - mark
    del attempts[mark:]                                                          # kasıtlı deneme "istenmeyen çıkış" sayılmaz
    check("sağlayıcı testi çevrimdışıyken SmartError döner (çökmez)", (not res.ok) and res.error is not None,
          f"{res.error.code if res.error else ''}; {probed} bağlantı denemesi engellendi")

    proj = ctx.project
    proj.data.name = "Selftest v2"
    proj.mark_dirty()
    ctx.autosave.tick()
    check("otomatik kayıt: değişiklik sessizce kaydedildi", not proj.dirty and
          json.loads(proj.file_path.read_text(encoding="utf-8"))["name"] == "Selftest v2", f"halka: {len(ctx.autosave.ring())}")
    first = ctx.snapshots.create("selftest")
    proj.data.name = "Selftest v3"
    ctx.snapshots.restore(first.id)
    check("snapshot geri yükleme", proj.data.name == "Selftest v2", f"{len(ctx.snapshots.list())} snapshot")

    ctx.jobs.register_handler("selftest", lambda job, jc: {"ok": True})
    job = ctx.jobs.submit("selftest", title="selftest")
    deadline = time.time() + 5
    while not job.finished and time.time() < deadline:
        time.sleep(0.01)
    check("iş kuyruğu: iş çalıştı ve DB'ye yazıldı", job.status.value == "done" and
          ctx.project.db.get_job(job.id)["status"] == "done")
    s = ctx.sysmon.sample()
    check("sistem izleyici", s.get("ram_total") is not None, f"cpu={s.get('cpu')} gpu={s.get('gpu')}")


def _selftest_recorder(ctx, check, tmpdir: str) -> None:
    """Adım 6: kayıt motoru **sahte mikrofonla** (gerçek aygıta açılmaz), .wav/PCM_24, LUFS ölçümü, aygıt sorgusu."""
    import numpy as np
    import soundfile as sf
    from kadraj.core import i18n
    from kadraj.modules.recorder import devices as rec_devices
    from kadraj.modules.recorder.dsp import measure_loudness
    from kadraj.modules.recorder.engine import RecorderEngine

    class _Stream:
        def __init__(self, cb):
            self.cb = cb

        def start(self): ...
        def stop(self): ...
        def close(self): ...

    holder: dict = {}

    def factory(sr, ch, dev, bs, cb):
        holder["s"] = _Stream(cb)
        return holder["s"]

    engine = RecorderEngine(factory)
    engine.configure(sample_rate=48000, channels=1)
    out = Path(tmpdir) / "selftest_kayit.wav"
    engine.start_recording(out)
    t = np.arange(48000 * 2) / 48000
    sig = (0.1 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    for i in range(0, len(sig), 1024):
        blk = sig[i:i + 1024, None]
        holder["s"].cb(blk, len(blk), None, None)
    res = engine.stop_recording()
    engine.stop()
    info = sf.info(out)
    lufs = measure_loudness(sf.read(out, dtype="float32")[0], 48000).integrated
    check("ses kaydı: sahte mikrofon → .wav PCM_24 48 kHz", out.suffix == ".wav" and info.subtype == "PCM_24"
          and info.samplerate == 48000 and res.frames == len(sig), f"{lufs:.2f} LUFS (beklenen -23.0)")
    check("LUFS ölçümü referansla uyumlu", abs(lufs + 23.0) < 0.1)
    try:
        n = len(rec_devices.list_input_devices())
        detail = f"{n} giriş aygıtı"
    except Exception as exc:                       # PortAudio yok → SmartError (çökme değil)
        detail = f"aygıt yok/backend yok: {getattr(exc, 'code', type(exc).__name__)}"
    check("giriş aygıtı sorgusu çökmeden döner", True, detail)
    svc = ctx.service("recorder")
    check("recorder modülü kayıtlı, servis başladı", ctx.registry.get("recorder") is not None and svc is not None,
          i18n.t("mod.recorder.title"))


def _selftest_editor(ctx, check, tmpdir: str) -> None:
    """Adım 7: zaman çizelgesi modeli + geri al, FFmpeg işleme (sentetik klip), platform şablonları, kodlayıcı seçimi, ek paket hatası."""
    import json
    import shutil
    import subprocess
    import sys
    import time
    from kadraj.core import i18n
    from kadraj.modules.editor import render
    from kadraj.modules.editor.probe import probe

    svc = ctx.service("editor")
    check("editor modülü kayıtlı, servis başladı", ctx.registry.get("editor") is not None and svc is not None,
          i18n.t("mod.editor.title"))
    from kadraj.core import components
    if getattr(sys, "frozen", False):          # paketlenmiş uygulama: bileşenler pakette olmalı, sistemden bir şey beklenmez
        missing = [n for n in ("ffmpeg", "ffprobe", "deno") if not components.have(n)]
        if components.ytdlp_choice() is None:
            missing.append("yt-dlp")
        check("pakete gömülü bileşenler bulundu (ffmpeg, ffprobe, deno, yt-dlp)", not missing,
              "eksik: " + ", ".join(missing) if missing else ", ".join(f"{n} {components.find(n).source}" for n in ("ffmpeg", "deno")))
    ffmpeg = components.ffmpeg_path()
    if not ffmpeg:
        check("FFmpeg işleme (atlandı: ffmpeg yok)", True)
        return
    src = Path(tmpdir) / "editor_selftest.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=160x90:r=10:d=2", "-f", "lavfi", "-i", "sine=f=440:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True, timeout=60)
    clip = svc.add_media(str(src))
    svc.set_playhead(1.0)
    left_right = svc.split_at_playhead()
    svc.undo()
    check("zaman çizelgesi: ekle/böl/geri al + proje dosyasına yazıldı", clip is not None and left_right is not None and
          len(svc.tl.track("V1").clips) == 1 and len(ctx.project.data.timeline["tracks"][0]["clips"]) == 1,
          f"{svc.tl.duration:.1f} sn · {svc.timecode(svc.tl.duration)}")
    svc.set_canvas(160, 90)
    s = svc.template_settings("youtube", "fast")
    est = svc.estimate(s)
    job = svc.start_render(s)
    deadline = time.time() + 30
    while job is not None and not job.finished and time.time() < deadline:
        ctx.main_thread_pump() if hasattr(ctx, "main_thread_pump") else time.sleep(0.02)
    res = (job.payload.get("_result") or {}) if job is not None else {}
    out = Path(res.get("output", ""))
    ok = bool(res) and out.suffix == ".mp4" and out.exists() and abs(probe(str(out)).duration - 2.0) < 0.5
    check("FFmpeg işleme: sentetik klip → 07_Exports/*.mp4", ok, f"{res.get('backend', '?')} · tahmin {est.size_bytes // 1024} KB")
    enc = render.choose_encoder("h264")
    check("kodlayıcı seçimi (NVENC→VAAPI→CPU)", enc.backend in render.BACKENDS, f"{enc.name} ({enc.backend})")
    check("7 platform şablonu", list(render.TEMPLATES) == ["youtube", "shorts", "reels", "tiktok", "podcast", "whatsapp", "archive"])
    from kadraj.modules.editor import automations
    sys.modules["librosa"] = None
    try:
        automations.detect_beats(str(src))
        detail, good = "hata atılmadı", False
    except Exception as exc:
        good = getattr(exc, "code", "") == "editor.opt_librosa" and bool(exc.actions) and exc.actions[0].kind == "copy"
        detail = getattr(exc, "code", type(exc).__name__)
    finally:
        sys.modules.pop("librosa", None)
    check("ağır paket yok → SmartError + 'paketi kur' eylemi", good, detail)
    ctx.project.save(silent=True)                  # değişiklik bırakma: pencere kapanışı "kaydedilmemiş" iletişim kutusu açmasın


def _selftest_ai(ctx, check, tmpdir: str) -> None:
    """Adım 8b: sağlayıcılar (sahte HTTP), fotoğraf istem hattı, dizi durum makinesi + kota, düğüm akışı. Gerçek ağ/anahtar kullanılmaz."""
    import base64
    import io
    import json
    import httpx
    from PIL import Image
    from kadraj.ai_engine.nodes.executor import Executor
    from kadraj.ai_engine.nodes.graph import Graph, default_flow
    from kadraj.ai_engine.pipeline import quota
    from kadraj.ai_engine.pipeline.gateway import AIGateway
    from kadraj.ai_engine.pipeline.machine import MachineError, PipelineMachine, SeriesProject
    from kadraj.ai_engine.prompt_adapter import PromptSpec
    from kadraj.ai_engine.providers.install import install_all
    from kadraj.modules.photo import imageops

    photo, series = ctx.service("photo"), ctx.service("series")
    ids = {p.id for p in ctx.providers.list()}
    need = {"anthropic", "openai_compat", "openai_image", "ollama", "bfl_flux", "google_imagen", "comfyui", "elevenlabs", "video"}
    check("AI sağlayıcıları kayıtlı (8 + OpenAI görsel)", photo is not None and series is not None and need <= ids, f"{len(ids)} sağlayıcı")
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (40, 90, 200)).save(buf, format="PNG")
    png = buf.getvalue()

    def route(r: httpx.Request) -> httpx.Response:
        if r.url.host == "api.bfl.ai":
            return httpx.Response(200, json={"id": "i", "polling_url": "https://api.bfl.ai/g"}) if r.method == "POST" else \
                httpx.Response(200, json={"status": "Ready", "result": {"sample": "https://cdn.bfl.ai/o.png"}})
        if r.url.host == "cdn.bfl.ai":
            return httpx.Response(200, content=png)
        if r.url.host == "api.anthropic.com":
            return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {"input_tokens": 9, "output_tokens": 3}})
        return httpx.Response(404, json={})
    install_all(ctx.providers, settings=ctx.settings, transport=httpx.MockTransport(route), sleep=lambda s: None)
    ctx.vault.set_secret("bfl_flux", "selftest-flux-key")
    ctx.vault.set_secret("anthropic", "selftest-claude-key")
    gw = AIGateway(ctx.providers, ctx.vault, ctx.settings, db=lambda: ctx.project.db)
    gw.local_up.update({"comfyui": False, "ollama": False})
    spec = PromptSpec.create(subject="eski bir vapur", lighting="sisli sabah", style="sinematik", aspect="16:9")
    res, adapted = gw.image(spec, prefer_model="flux")
    out = imageops.save_bytes(res.images[0], ctx.project.subfolder("03_AI_Images"), "selftest")
    llm = gw.llm("selam", tier="cheap")
    usage = ctx.project.db.usage_summary()
    check("fotoğraf: istem → FLUX (sahte HTTP) → 03_AI_Images + api_usage", out.exists() and adapted.model == "flux" and gw.last_provider == "anthropic"
          and usage["calls"] == 2 and usage["cost_usd"] > 0.04, f"{usage['calls']} çağrı · ${usage['cost_usd']:.3f} · {llm.model}")
    cover, ext = imageops.make_cover(out, "SELFTEST")
    check("YouTube kapağı 1280×720", Image.open(io.BytesIO(cover)).size == (1280, 720), ext)
    m = PipelineMachine(SeriesProject(idea="x"))
    m.advance()
    try:
        m.advance()
        gated = False
    except MachineError:
        gated = True
    check("dizi durum makinesi: onaysız aşama geçilmez", gated and m.stage.value == "screenplay")
    share = quota.plan_share(quota.estimate(video_seconds=60, images=4), spent=38.0, settings=type("S", (), {"get": lambda self, k, d=None: 100 if k == "plan.budget_usd" else None})())
    check("plan/kota tahmini", share.level == "ok" and share.before_pct == 38.0 and abs(share.project_pct - 6.2) < 0.2,   # sayısal; metne bakılmaz
          f"%{share.project_pct:.1f} (daha önce %{share.before_pct:.0f})")
    g = Graph()
    g.add("logline", id="l", text="merhaba")
    g.add("output", id="o")
    g.connect("l", "text", "o", "in")
    run = Executor(gw, Path(tmpdir) / "nodes").run(g)
    check("düğüm akışı çalıştı (topolojik)", run.status == "done" and Path(run.results["o"].outputs["files"][0]).exists() and not default_flow().validate())
    ctx.project.save(silent=True)


def selftest_download(argv: List[str]) -> int:
    """Paketlenmiş uygulamada gerçek indirme: ``--selftest-download URL [DIR]``. Ağ açıktır; ayarlar geçici ev dizininde.

    Bileşenler (ffmpeg, deno, yt-dlp) ``core/components.py``'den çözülür; sonuç dosyası ffprobe ile doğrulanır.
    Çıktının son satırı ``İNDİRME TAMAM`` ise başarılıdır."""
    import subprocess
    i = argv.index("--selftest-download")
    rest = [a for a in argv[i + 1:] if not a.startswith("--")]
    if not rest:
        print("kullanım: --selftest-download URL [DIR]")
        return 2
    url = rest[0]
    tmp = tempfile.TemporaryDirectory(prefix="kadraj-indirme-", ignore_cleanup_errors=True)
    out_dir = Path(rest[1]) if len(rest) > 1 else Path(tmp.name) / "indirilen"
    os.environ["KADRAJ_HOME"] = str(Path(tmp.name) / "home")
    os.environ["KADRAJ_NO_UPDATE"] = "1"
    from kadraj.core import components
    from kadraj.core.contracts import SmartError
    from kadraj.modules.downloader import service as S
    print("bileşenler:", {k: (v["source"], v["version"]) for k, v in components.status().items()})
    svc = S.DownloaderService()
    try:
        info = svc.fetch_info(url)
        print(f"başlık: {info.title} · en yüksek {info.max_height}p · çözücü eksik: {info.degraded}")
        print("kaliteler:", [(q.key, q.available, q.actual_height) for q in svc.quality_options(info)])
        req = S.DownloadRequest(url=url, mode=S.MODE_AV, quality=S.QUALITY_BALANCED)
        out_dir.mkdir(parents=True, exist_ok=True)
        opts = S.build_ydl_options(req, out_dir=out_dir)
        opts["logger"] = S.Collector()
        with svc._ydl(opts) as ydl:
            res = ydl.extract_info(url, download=True)
            files = S._collect_files(res or {}, ydl, S.MODE_AV)
    except SmartError as exc:
        print(f"HATA: {exc.title} — {exc.summary}\n  neden: {exc.cause}\n  " + "\n  ".join(exc.steps) + f"\n  ayrıntı: {exc.technical[-300:]}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"HATA: {type(exc).__name__}: {exc}")
        return 1
    ffprobe = components.ffprobe_path()
    if not files or not ffprobe or not Path(files[0]).is_file():
        print(f"HATA: dosya ya da ffprobe yok: {files} {ffprobe}")
        return 1
    probe = subprocess.run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height:format=duration",
                            "-of", "compact", files[0]], capture_output=True, text=True)
    print(probe.stdout.strip())
    ok = probe.returncode == 0 and "codec_type=video" in probe.stdout and "codec_type=audio" in probe.stdout
    print(f"dosya: {files[0]} ({Path(files[0]).stat().st_size} bayt)")
    print("İNDİRME TAMAM" if ok else "HATA: dosya oynatılabilir görünmüyor")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _utf8_stdio()
    from kadraj import frozen
    frozen.apply()                                       # yalnızca paketlenmiş uygulamada: alt süreç ortamı / konsol penceresi
    if "--selftest-download" in argv:
        return selftest_download(argv)
    if "--selftest" in argv:
        return selftest(argv)
    from kadraj import device
    if device.is_android():
        return _run_android(argv)
    from kadraj.ui.shell.app import run
    return run(["kadraj"], splash=not ("--launcher" in argv or "--no-splash" in argv),
               mode=_arg_value(argv, "--mode"))


def _run_android(argv: List[str]) -> int:
    """Android: açılışı durduran hata kara ekran bırakmasın; ekranda gösterilir ve Belgeler/KADRAJ-hata.txt'ye yazılır."""
    try:
        from kadraj.ui.shell.app import run
        return run(["kadraj"], splash=True, mode=_arg_value(argv, "--mode"))
    except Exception:  # noqa: BLE001
        import traceback
        from kadraj import crashlog
        crashlog.show_fatal(traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
