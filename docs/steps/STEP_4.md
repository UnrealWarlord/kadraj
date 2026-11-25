# Adım 4 — Medya İndirici (Haiku)

## DEVİR
**Yeni:** `kadraj/modules/downloader/{__init__,module,service,strings,range_bar,page_simple,page_legend}.py` · `requirements.txt` (`yt-dlp>=2026.10.26`) · `tests/downloader/{__init__,conftest,fakes,test_module,test_pure,test_service,test_range_bar,test_pages}.py` · `docs/steps/STEP_4.md`
**Çekirdek dışında değişen:** `tests/core/test_shell.py` — tek satır: indirici artık "yakında" kartı değil, öteki kartlar hâlâ "yakında" (bu test modül yokken yazılmıştı; koordinatör onaylasın)
**Test:** `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -o addopts="" -q tests/downloader` → `53 passed` · tüm ağaç → `461 passed, 1 failed` · başarısız tek test `tests/core/test_i18n.py` (`recorder.*` anahtarları: Sonnet'in o an yazdığı `recorder` şeridinden, bu adımdan değil)
**Kayıt:** `module.py` `register()` → `id="downloader"`, `order=10`, `requires=("ffmpeg",)`; sayfa ve servis fabrikaları tembel; yt-dlp hiçbir modülde üst düzeyde içe aktarılmaz (test var)
**Servis (`ctx.service("downloader")` → `DownloaderService`):** `probe(url)` arka planda; sinyaller `probed(VideoInfo)` / `probeFailed(SmartError)` · `fetch_info(url, playlist=)` · taslak: `set_url / set_mode / set_quality / set_format_ids / set_partial / set_range(start, end) / set_options(Options)` · `build_request(duration=, with_output=)` · `submit(request, title=) -> Job` · `submit_playlist(entries, request)` · `recent(limit) -> list[dict]` · `cancel(job_id)` · `default_output_dir()`
**İş:** `kind="download"`; yük `DownloadRequest.to_payload()`; sonuç `_result = {"path", "files", "folder"}`
**Saf yardımcılar (`service.py`):** `format_selector`, `build_ydl_options`, `command_preview`, `info_from_raw`, `estimate_bytes`, `parse_timecode`, `format_timecode`, `describe_length`, `parse_rate`
**Hata kodları:** `input.url_invalid` · `net.offline` · `ytdlp.*` (çözümleme ve indirme) · `disk.critical` (indirmeden önce; `storage/disk.py`)
**Ses:** "Sadece Ses" → yalnız `.wav`; `FFmpegExtractAudio` + `-ar 48000 -c:a pcm_s24le` (yerel ffmpeg ile doğrulandı: pcm_s24le, 48 kHz, 24 bit)
**Kullanılan yüzeyler:** `ctx.jobs.register_handler/submit/cancel/get/list/wait_idle`, `ctx.bus.subscribe(JOB_ADDED/JOB_UPDATED)`, `ctx.project.root`, `ctx.settings "project_root"`, `storage.folders.folder_for("download")`, `storage.disk.warning_for/human_size`
**Bilinen eksikler:**
- Gerçek ağ ve gerçek indirme denenmedi; testler sahte `YoutubeDL` ile çalıştı. Seçenekler gerçek yt-dlp'ye kurularak doğrulandı (ağ yok)
- Liste (playlist) her video için ayrı iş; kırpma (aralık) listeye uygulanmaz, UI aralığı tek videoda açar
- "En Yüksek" 4K (2160p) ile sınırlı; 2160p üstü indirilmez
- "Sadece Görüntü" 720p'de biçim yoksa boyut "bilinmiyor" yazar, indirme yine çalışır
- Legend'de ses örnekleme hızı seçimi yok (Basit sabit 48 kHz / 24-bit)
- "Son indirilenler" yalnızca oturumda bellekte; kalıcı geçmiş yok
- Legend kuyruğunda yalnız iptal var; duraklat/devam düğmesi yok
- Ekran görüntüsü alınmadı; sayfalar offscreen açılıyor ve etkileşim testleri geçiyor

---
## Ayrıntılar (karşı thread okumaz)

**Durum:** tamamlandı, commit atılmadı.

**Dosyalar**
- `service.py`: `DownloaderService(QObject)`. Taslak (`Draft`) ve son bilgi servisin içinde; sayfalar yalnızca görünüm. Çözümleme `threading.Thread` ile çalışır; sonuçlar sinyalle gelir, eski sonuçlar belirteçle (`_token`) yok sayılır. İşleyici `_handle_download`: çıktı klasörü `01_Downloads` (açık proje varsa `project.root` altında, yoksa `settings.project_root` altında, o da yoksa `~/Videos`); klasör işleyicide oluşturulur. İlerleme kancası iptali yakalar (`_Cancelled`, sessiz `{}` döner). yt-dlp hataları `translate` ile `SmartError`'a çevrilir; araç `ffmpeg` mesajında geçiyorsa `ffmpeg`, değilse `yt-dlp`.
- `range_bar.py`: iki tutamaçlı aralık; sürükleme, Yukarı/Aşağı ile etkin tutamaç, Sol/Sağ 1 sn (Shift 10 sn); en az 1 sn aralık.
- `page_simple.py`: büyük bağlantı kapsülü + Yapıştır; "… videosu bulundu · süre" çipi; 3 mod kartı; "Sadece bir kısmı" + aralık çubuğu + özet cümlesi; 3 kalite kartı (boyut tahminli); "Son indirilenler" (Klasörde Göster); şeftali İndir; hedef klasör yazısı. Teknik terim yok (yalnız `K.*` metinleri).
- `page_legend.py`: mono URL + "-F Listele"; meta satırı; biçim tablosu (ID, EXT, ÇÖZ., FPS, VCODEC, ACODEC, BOYUT, NOT; çoklu seçim → `format_ids`); biçim ve çözünürlük açılır listeleri; IN/OUT alanları + aralık çubuğu; seçenek kutuları (altyazı dilleri, kapak, gömme, liste, 5 MB/s); komut önizlemesi (`command_preview`, seçilebilir metin); kuyruk tablosu (durum, yüzde, iptal).
- `strings.py`: `K` sabitleri (`K.URL_INVALID_TITLE` → `downloader.url.invalid.title`); TR/EN aynı anahtar kümesi; "KADRAJ" yok.
- `module.py`, `requirements.txt`, `__init__.py`.

**Kararlar**
1. Aralık: bitiş klip süresinin 0,5 sn içindeyse "sona kadar" (`end=None`); başlangıç 0,5 sn altındaysa 0. Bitiş − başlangıç < 1 sn ise `ValueError` (sayfa kısa uyarı gösterir).
2. Biçim seçimi: Legend'de biçim seçilmişse `format_ids` birleşimi (`137+140`) kullanılır; seçim yoksa mod + kalite.
3. Liste: sayfa her girdiyi ayrı `"download"` işi olarak kuyruğa koyar; her iş kendi `DownloadRequest`'ini taşır (`playlist=False`).
4. Boyut tahmini yalnız gösterim içindir (`estimate_bytes`); bilinmiyorsa "bilinmiyor".
5. Yeniden deneme: iş kuyruğunda `max_retries=1`; yalnız `retryable` hatalar tekrarlanır.

**Doğrulama**
- Sahte `YoutubeDL` (`tests/downloader/fakes.py`): çözümleme, playlist listesi, çevrimdışı (`URLError` → `net.offline`), yt-dlp hatası (`ytdlp.unavailable`, `ytdlp.age_restricted`), iptal, kritik disk (`disk.critical`), aralık/seçenek aktarımı, gerçek `JobQueue` üzerinde tek iş ve playlist (2 iş), taslak paylaşımı, sayfalar kabuk fabrikasıyla iki modda açılıyor (`build_module_page`).
- Gerçek yt-dlp (2026.10.26) ile `YoutubeDL(opts)` yapılandırması: av / audio / video seçenekleri kabul edildi (ağ çağrısı yok).
- Yerel ffmpeg ile dönüşüm zinciri (`-vn -acodec pcm_s16le -ar 48000 -c:a pcm_s24le`): çıktı `pcm_s24le`, 48 kHz, 24 bit.
- Test sayıları: `tests/downloader` 53 passed.

**Çekirdek dosyalara dokunulmadı.** Tek değişen test: `tests/core/test_shell.py` (yukarıda).
