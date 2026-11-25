# Adım 5 — Ayarlar ekranı (Haiku)

## DEVİR
**Yeni:** `kadraj/modules/settings/{__init__,module,service,strings,guide,page_simple,page_legend}.py` · `requirements.txt` · `tests/settings/{__init__,conftest,fakes,test_pure,test_module,test_service,test_guide,test_pages}.py` · `docs/steps/STEP_5.md`
**Çekirdek dışında değişen:** yok (`tests/core` ve çekirdek dosyalara dokunulmadı)
**Test:** `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -o addopts="" -q tests/settings` → `48 passed` · tüm ağaç (sırayla, her biri ayrı): core `149` · storage `97` · security `165` · recorder `253` · downloader `53` · settings `48` → toplam `765 passed`, başarısız yok (core ~90 sn)
**Karar:** Legend bölümleri (7) ilk seçildiklerinde kurulur; açılışta yalnız "Arayüz" kurulur. Gerekçe: kabuk testlerinde her pencerede tüm bölümler kurulunca QSS yeniden biçimlendirme süresi üç katına çıkıyordu (HEAD kopyasında kabuk testleri 32 sn → 74 sn; bölümler tembel kurulunca 29 sn).
**Karar:** Yerel algılama ve araç sürümü sayfa açılışında yalnız bir kez çalışır (`ensure_local/ensure_tools`); yenileme düğmeleri her zaman çalıştırır.
**Kayıt:** `module.py` → `id="settings"`, `order=90`, `workspace="system"`, `icon="gear"`; ana ekranda kart değildir (üst çubuk dişlisi açar)
**Servis (`ctx.service("settings")` → `SettingsService`):** `changed(topic)` ("keys"|"local"|"tools"|"folder"|"fallback"|"profiles"|"view") · `pingDone(id, PingResult)` · kasa: `key_rows()`, `save_key/delete_key`, `key_preview(id)` (yalnız ilk 6 + … + son 4), `vault_state()` · sağlayıcı: `test_connection(id)`, `test_all()` (arka plan) · yerel: `refresh_local()`, `local_runtimes()`, `start_local(id)`, `set_model`, `set_whisper_model` · araçlar: `refresh_tools()`, `tool_info()`, `update_ytdlp()` (iş kuyruğu: kind `tools.ytdlp`, pip) · klasör/önbellek: `set_project_root`, `folder_info()`, `cache_bytes()`, `clean_cache()` · sıra: `chain_ids(kind)`, `set_chain(kind, ids)` · profil: `profile_list/create_profile/switch_profile/delete_profile` · görünüm: `set_ui_mode`, `set_a11y(key, v)`, `set_tips`
**Sayfalar:** Basit `SettingsSimplePage` (mod kartları, kasa satırları, yerel AI, klasör + temizlik, görünüm, profil, araçlar) · Legend `SettingsLegendPage` (sol menü 7 bölüm; API tablosu, yerel tablo, fallback listesi, yollar, güncellemeler, profiller)
**Rehber:** `guide.py` — `GuideView` (QPainter 5 adım, sağlayıcı sekmeleri, geri/ileri, adresi kopyala), `GuideDialog` (Basit), Legend'de satır içi çekmece
**Kullanılan yüzeyler:** `ctx.vault` (`has_secret/masked/set_secret/get_secret/delete_secret`, varsa `status()`), `ctx.providers` (`list/test_connection/endpoint`), `ctx.set_ui_mode`, `ctx.theme.apply`, `Topics.A11Y_CHANGED`, `ctx.jobs.submit/register_handler`, `storage.{cache,disk,folders,profiles}`
**Karar:**
- Önbellek temizliği hedefi: açık proje; yoksa son açılan proje; ikisi de yoksa düğme "önce proje aç" der.
- yt-dlp güncellemesi pip ile iş kuyruğunda; çalışan süreç yeniden başlatılana dek eski sürüm kullanılır (metinle bildirilir).
- FFmpeg için otomatik kurulum yok; sistem paket yöneticisi komutları yalnızca gösterilir, çalıştırılmaz.
- Rehber adresleri: `console.anthropic.com`, `platform.openai.com`, `aistudio.google.com`, `elevenlabs.io`, `bfl.ai`, `pexels.com`, `pixabay.com`, `freesound.org` — yayından önce elle doğrulanmalı.
- Göz düğmesi tam anahtarı göstermez; varsayılan görünüm kasanın maskesi (`sk-••••1234`), göz açınca `sk-ant…1234`.
- Yerel başlatma: Ollama için `ollama serve` (alt süreç, kabuksuz); ComfyUI için Yollar'da klasör seçilmeli, yoksa talimat çıkar.
- Profil geçişi o profilin mod ve erişilebilirlik tercihlerini uygular; profil silmek kasadaki sırları silmez (ad alanı Adım 3'te temizlenir).
- Mod değişimi `ctx.set_ui_mode`; erişilebilirlik `ctx.theme.apply` + `A11Y_CHANGED` ile main_window ile aynı yoldan.
- Basit'te "Nasıl bir [KADRAJ] istersin?" başlığı `Lockup` ile çizilir; metin dizelerinde "KADRAJ" yok.

**Bilinen eksikler:**
- Sağlayıcı pingleri ve Ollama/ComfyUI algılama gerçek ağda denenmedi; testler sahte HTTP ve sahte ping kullanır
- Alt süreç başlatma (`ollama serve`, ComfyUI) gerçek ortamda çalıştırılmadı (Popen enjekte edildi)
- Yollar bölümünde render ve geçici klasör yalnız gösterilir, değiştirilemez
- Basit'te profil silme yok (Legend'de var)
- Ekran görüntüsü alınmadı; sayfalar offscreen açılıyor ve etkileşim testleri geçiyor

---
## Ayrıntılar (karşı thread okumaz)

**Durum:** tamamlandı, commit atılmadı.

**Dosyalar**
- `service.py`: `SettingsService(QObject)`. Saf yardımcılar: `mask_preview`, `initials`, `led_state`, `parse_ollama_models`, `parse_ffmpeg_version`, `install_hints`. Ağ (`http_get`), pip/ffmpeg (`run_cmd`), alt süreç (`popen`) ve ping (`ping_fn`) testlerde enjekte edilir. Ağ çağrıları iş parçacığında; sonuç sinyalle gelir.
- `guide.py`: `StepCanvas` beş adımı `QPainter` ile çizer (tarayıcı, menü, "Create Key" düğmesi, kopyalama, yapıştırma + onay). `GuideView` sekmeler ve gezinme; `GuideDialog` pencere.
- `page_simple.py`: mod kartları `_Preview` ile her modun kendi tokenlarından çizilir. Kasa satırı: baş harf avatarı (kind'a göre aksan), ad, kullanım, maskeli hap, göz, LED + kod, gecikme, "Bağlantıyı Test Et", anahtar ekle/değiştir, sil (onaylı), "Nereden Alırım?".
- `page_legend.py`: sol menü (7 bölüm). Kasa tablosu; satır içi rehber çekmecesi; yerel çalışma zamanı tablosu; fallback sırası (yukarı/aşağı, kaydet); yollar (proje, önbellek, render, geçici, ComfyUI) ve klasör seçimi; güncellemeler tablosu ve FFmpeg talimat kutusu; profiller tablosu (yeni, geç, sil).
- `strings.py`: `K` sabitleri; TR/EN 135 anahtar eşit; "KADRAJ" yok.
- `module.py`, `__init__.py`, `requirements.txt` (ek bağımlılık yok).

**Güvenlik notları**
- Düz anahtar yalnızca `get_secret` ile, yalnızca önizleme üretmek için okunur; kayıt, log veya tablo hücresine yazılmaz.
- Kasa kilitliyse yazma `SmartError` ile reddedilir; okuma yöntemleri boş döner (arayüz çökmez).
- Yerel başlatma kabuk kullanmaz (`shell=False` eşdeğeri: argüman listesi); `ollama` yolu `shutil.which` ile çözülür.
- Önbellek temizliği yalnız `cache.clean` ile (`.kadraj/cache`, `06_Proxies`, yarım indirmeler); proje dosyası ve kayıtlar taranmaz.

**Doğrulama**
- `tests/settings`: 48 passed. Saf yardımcılar, modül sözleşmesi, servis (kasa kilitliyken, ping başarı/başarısızlık/istisna, arka plan pingi sinyali, yerel algılama, Ollama/ComfyUI başlatma, araç sürümü, pip işi başarı/başarısızlık, klasör ve önbellek, sağlayıcı sırası, erişilebilirlik yayını, mod değişimi, profil geçişi), rehber (adım gezintisi, adres kopyalama, her adımın çizimi), iki sayfa kabuk fabrikasıyla iki modda açılıyor.
- `tests/downloader`: 53 passed.
- Dize anahtarları: `K.*` referansları tanımlı; TR/EN anahtar kümeleri eşit.
