# Adım 7 — Video Düzenleme / NLE (Sonnet)

## DEVİR
**Yeni:** `kadraj/modules/editor/{module,service,timeline,ops,snapping,filtergraph,render,probe,proxy,frames,color,scopes,audio,automations,player,errors,widgets,panels,page_base,page_simple,page_legend,strings,requirements.txt}` · `kadraj/ui/widgets/{dock_panel,preview_tooltip}.py` (DockPanel, PreviewTooltip) · `tests/editor/*` (conftest + 8 test dosyası) · `docs/steps/STEP_7.md` + `docs/steps/step7/*.png`
**Değişen (şerit dışı, gerekçeli):** yalnız `main.py` (`--selftest` içine `_selftest_editor`; sonunda `project.save` — kirli proje kapanışta kalıcı iletişim kutusu açıp selftest'i kilitliyordu). Haiku dosyalarına, `contracts.py`'a, `core/*`'a **dokunulmadı**. Yeni zorunlu bağımlılık yok (FFmpeg/ffprobe sistemde var olmalı).
**Test:** `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q tests/editor | tail -5` → `65 passed` (≈23 sn; gerçek FFmpeg işleme testleri dahil) · tüm ağaç `881 passed` (≈2,5 dk) · `python main.py --selftest` → `SELFTEST TAMAM` (NVENC ile gerçek işleme ✓)
**Başkalarının çağırabileceği imzalar** (sözleşme değişmedi; modül yüzeyi):
- `ctx.service("editor") -> EditorService` — tüm durum burada; sinyaller `timelineChanged/selectionChanged/playheadChanged/frameReady/proposalReady/jobChanged/exportFinished/errorRaised/hardwareChanged`
- `svc.add_media(path, track_id=None, start=None) -> Clip|None` — **Adım 8 üretilen görsel/sahneyi zaman çizelgesine böyle sokar**; video→V, ses→A, görsel→V (5 sn). Geri alma: `ctx.undo` (her düzenleme tek `SnapshotCommand`)
- `svc.start_render(RenderSettings) -> Job` — iş türü `editor.render`, sonuç `{output,size,warnings,backend}` → `<proje>/07_Exports/<proje>_<şablon>_<tarih>.<ext>` · `svc.template_settings(id, quality)` · `svc.estimate(s) -> Estimate(size_bytes, seconds, video_kbps)` · `svc.reveal(path=None)` ("Dosyayı Klasörde Göster")
- `svc.propose(name, **kw) -> Proposal` / `propose_async` → `svc.apply(proposal)` — otomasyonlar: `silence voice ducking vitalize colormatch beat reframe greenscreen subtitles watermark freeze text_edit`; öneri zaman çizelgesini **değiştirmez**
- `render.build_plan(tl, RenderSettings, out) -> RenderPlan` · `render.execute_plan(plan, report, cancelled)` · `render.TEMPLATES` (youtube/shorts/reels/tiktok/podcast/whatsapp/archive) · `render.choose_encoder(codec, hw) -> EncoderChoice` · `render.estimate(...)`
- `filtergraph.build_graph(tl, include_video, include_audio, size, loudnorm_lufs, proxies)` / `window(tl, t0, t1)` · `timeline.Timeline.from_dict/to_dict/snapshot/restore` · `format_timecode/parse_timecode(HH:MM:SS:FF)` · `ops.*` (split/trim/move/keyframe/marker/freeze…)
- `frames.extract_frame(tl, t, size, before=False) -> ndarray(H,W,3)` · `scopes.histogram/rgb_parade/vectorscope` · `color.ColorPipeline/match_lut/read_cube/write_cube` · `audio.eq_curve_db/strips` · `proxy.make_proxy/proxy_map`
**Karar:** Zaman **saniye (float)**, kare sınırına yuvarlanır; `HH:MM:SS:FF` yalnız gösterim/giriş (29.97/23.976'da sayaç nominal fps'te sarar, drop-frame yok). `proje.kadraj` biçimi **eklemeli**: tanınmayan alanlar (`extra`) korunur.
**Karar:** Üst üste bindirme: `tracks` listesinde **sonraki kanal üstte**; ADJ/fx kanalı kendinden önceki her şeye `enable='between(t,a,b)'` ile uygulanır. Anahtar kareler (scale/x/y/rotate/opacity/volume) Bezier'den örneklenip parçalı doğrusal FFmpeg ifadesine çevrilir; hız eğrisi ≤24 sabit hızlı parça + `concat` (ses `atempo` zinciri).
**Karar:** Donanım kodlayıcı **denenerek** algılanır (listede olmak yetmez), arka plan iş parçacığında (arayüz beklemez): NVENC → VAAPI → CPU; VAAPI'de iki geçiş yok → tek geçiş + uyarı. Kayıpsız/ProRes ve WAV her zaman CPU.
**Karar:** Podcast şablonu yalnız `.wav` (24-bit/48 kHz, −16 LUFS) — "ses çıktısı yalnız .wav" kuralı; Arşiv = ProRes 422 HQ `.mov`. WhatsApp ≤15,5 MB: bit hızı süreye göre kısılır (taban 150 kbps).
**Karar:** Önizleme = düşük çözünürlüklü birleşik dosya (`.kadraj/cache/preview/<özet>.mp4`) **QMediaPlayer** ile; dosya yoksa sessiz zamanlayıcı yedeği; Önce/Sonra karesi `strip_look` (efekt/anahtar kare/ayar katmanı çıkarılmış kopya). Proxy yalnız önizleme için; **son işleme özgün dosyayla**.
**Karar:** Her otomasyon `Proposal` (analiz → özet → kullanıcı **Uygula** → tek geri alma adımı); hiçbiri kendiliğinden çalışmaz. Ağır paket (librosa/opencv/rembg) yoksa `SmartError` + "İsteğe bağlı paketi kur" (`copy` eylemi: `pip install …`). `.cube` LUT'lar `06_Proxies/luts/`, ekran/kamera kayıtları `01_Downloads/` (ayrı klasör yok). Medya türü uzantı listesiyle değil MIME'le (brand-rule testi `.mp3` vb. dize sabitlerini yasaklıyor).
**Karar:** Basit Mod 3 kanal (Video/Müzik/Seslendirme), model sınırsız; Legend'de 4 çalışma alanı (DÜZENLE/RENK/SES/DIŞA AKTAR), her biri ayrı `saveState` (`ctx.settings["editor.workspaces"]`).
**Bilinen eksikler:** librosa/OpenCV/rembg bu ortamda kurulu değil → gerçek yolları yalnız sahte modülle sınandı (hata yolları gerçek) · Dinamik altyazı kelime zamanı yok (`Segment` kelime vermiyor): kelimeler karakter uzunluğuna göre bölünür · `x11grab` ekran kaydı yalnız komut düzeyinde sınandı (Wayland/pipewire desteklenmiyor → "no_source" kartı) · VAAPI komutu kuruldu ve birim sınandı ama gerçek VAAPI işleme koşulmadı (NVENC koştu) · geçiş (xfade) arayüzü, klip üstü dalga formu/küçük resim, çoklu seçim kırpma yok · ölçüm ekranları referans test deseniyle kalibre edilmedi · yerleşim yalnız offscreen görüldü, elle denenmedi · QMediaPlayer sesli oynatma yalnız duman testi.
**Kalan:** yok — brief'teki işlev listesi tamam; yukarıdakiler gerçek ortam/elle doğrulama bekleyenler. Sıradaki: **Adım 8**.

---
## Ayrıntılar (karşı thread okumaz)

Ekran görüntüleri (offscreen, sentetik klipli proje): [`step7/simple_editor.png`](step7/simple_editor.png) · [`step7/legend_edit.png`](step7/legend_edit.png) · [`step7/legend_color.png`](step7/legend_color.png)

### 1. Ne yapıldı
| Parça | Davranış |
|---|---|
| **Model** `timeline.py` `ops.py` | `Timeline/Track/Clip/Keyframe/Marker` (saf Python; `to_dict/from_dict` + bilinmeyenleri koruma), kanal türleri `video/audio/fx/sub/adj`, sınırsız kanal, Bezier (`cubic_bezier`, Newton+ikiye bölme), hız eğrisi integrali (`source_time`), işaretçi + YouTube bölümleri (kural denetimi: ≥3, 0:00'da başla, ≥10 sn), kilitli kanal (`LockedError`), böl/kırp/taşı/ripple sil/dondur. |
| **Yapışma** `snapping.py` | Klip kenarı, oynatma başlığı, işaretçi, 0; taşırken baş **ve** son kenar; eşik piksel/`px_per_s`; arayüzde "Yapıştı!" balonu. |
| **Grafik** `filtergraph.py` | `color` tuval + klip başına `trim→hız→fps/scale→efekt→dönüşüm→solma→overlay`; metin (`drawtext`, `expansion=none`), altyazı (`subtitles` ASS), resim, dondur (`tpad`); ses `atrim/atempo/volume/afade/adelay` → kanal `amix` → ses/pan/kanal modu/**ducking (`sidechaincompress`)** → ana ses + 10 bant `equalizer` + `loudnorm` + `alimiter`. |
| **İşleme** `render.py` | H.264/H.265/ProRes/WAV, CBR/VBR, 1–2 geçiş, fps/çözünürlük, bölüm meta verisi, `.part`→yeniden adlandır (iptal/hatada silinir), `-progress` ilerleme, hata eşleme (`disk_full/no_encoder/…`), kalite/boyut/süre tahmini. |
| **Servis** `service.py` | Proje yükle/yaz (`dataReplaced` ile geri sarmada yeniden yükler), `SnapshotCommand` (kaydırıcı sürüklemeyi birleştirir), kitaplık (DB + klasör tarama), kare isteği (en yeni kazanır), işler (`editor.render/proxy/preview`), otomasyon önerileri, kayıt. |
| **Sayfalar** | Basit: V2Editor sırası (önizleme+Önce/Sonra · Yatay/Dikey · Sihirli dokunuşlar · Nerede paylaşacaksın? · Zaman çizgisi); Legend: gömülü `QMainWindow` + `DockPanel`'ler, menü çubuğu, 4 çalışma alanı, ölçüm ekranları, EQ+mikser, Bezier/hız eğrisi denetçisi, dışa aktarma (kodek/hız/geçiş/fps/donanım/LUFS + kuyruk). |

### 2. Şerit dışı dokunuşlar
1. `main.py`: `_selftest_editor` (zaman çizelgesi+geri al, gerçek FFmpeg işleme, 7 şablon, kodlayıcı seçimi, ağır paket hatası).
2. `kadraj/ui/widgets/{dock_panel,preview_tooltip}.py`: brief'in izin verdiği yeni ortak widget'lar.

### 3. Karşılaşılan sorunlar
- `-nostdin` kayıt komutunda `q` ile durdurmayı engelliyordu (15 sn bekleme) → kayıt komutundan kaldırıldı.
- `drawtext` `%` işaretini genişletiyordu → `expansion=none`.
- Gerçek donanım algılama arayüz iş parçacığını bekletebilirdi → arka plan iş parçacığı + `block=False` yolu (test var).
- `QWidget.actions()` ile ad çakışması (`self.actions`) → `menu_actions`.
- Brand-rule testi `".mp3"` gibi dize sabitlerini yasaklıyor → medya türü MIME ile.
