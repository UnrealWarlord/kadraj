# Adım 6 — Ses Kayıt Stüdyosu (Sonnet)

## DEVİR
**Yeni:** `kadraj/modules/recorder/{module,service,engine,dsp,devices,takes,teleprompter,srt,mastering,widgets,page_base,page_simple,page_legend,strings,requirements.txt}` · `kadraj/ai_engine/providers/whisper_local.py` · `tests/recorder/*` (12 dosya) · `docs/steps/step6/*.png`
**Değişen (şerit dışı, gerekçeli):** `ui/theme/manager.py` (**gerileme düzeltmesi + gereksiz QSS yüklemeyi atla**) · `main.py` (selftest) · `conftest.py` (silinen widget temizliği) · `tests/core/{test_theme,test_i18n,test_shell}.py` · yeni bağımlılık YOK (DSP numpy; scipy kullanılmadı)
**Test:** `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q tests/recorder | tail -5` → `253 passed` (≈19 sn) · tüm ağaç `765 passed` · `python main.py --selftest` → `SELFTEST TAMAM` (ses kaydı −23.00 LUFS ✓)
**Başkalarının çağırabileceği imzalar** (sözleşme `core/contracts.py` değişmedi; bunlar modül yüzeyi):
- `ctx.service("recorder") -> RecorderService` — tüm durum burada; sayfalar yalnız bağlanır (`ticked/stateChanged/takesChanged/settingsChanged/errorRaised/micTestFinished/jobChanged` sinyalleri)
- `svc.start_recording() -> Path` / `stop_recording() -> Take|None` — `.wav`, `02_Audio_WAV`; aygıt/disk yoksa `SmartError` atar (`audio.no_input`, `audio.no_backend`, `disk.full`…)
- `svc.start_mic_test(seconds=5, calibration=False)` / `finish_mic_test() -> LevelAnalysis` / `apply_suggested_gain()` — yalnız **öneri**; uygulamak kullanıcı eylemi
- `svc.transcribe(take, language=None, model=None) -> Job` — iş türü `recorder.transcribe`, çıktı `<ad>.srt` (iş kuyruğunda)
- `svc.master(take, settings=None) -> Job` — iş türü `recorder.master`, çıktı `<ad>_master.wav`, orijinal korunur
- `svc.snapshot() -> RecorderSnapshot` · `svc.takes() -> list[Take]` · `svc.prompter` (`Prompter`) · ayarlar `ctx.settings["recorder.*"]`
- `dsp.measure_loudness(samples, sr) -> LoudnessSnapshot(momentary, short_term, integrated, true_peak_db)` — BS.1770-4 LUFS/TP; **Adım 7 mikseri/EQ için yeniden kullanılabilir**
- `dsp.analyze_level(samples, sr, current_gain_db=0) -> LevelAnalysis` · `dsp.NoiseGate(sr, GateParams, enabled)` · `dsp.LevelMeter` — saf numpy, Qt'siz
- `mastering.master_file(src, settings=MasterSettings(), on_progress=, is_cancelled=) -> MasterResult` — FFmpeg iki geçiş, −14 LUFS
- `WhisperLocalProvider.transcribe(path, language=, model_size=, on_progress=, is_cancelled=) -> Transcript` — `ctx.providers.get("whisper_local")`
**Karar:** Basit'te kapı ön ayarı sabit yumuşak (−42 dB, aralık −40 dB) ve **varsayılan KAPALI**; Legend'de 5 parametre kullanıcıdan.
**Karar:** Auto-gain/mikrofon testi yalnız öneri üretir; kazanç kendiliğinden değişmez ("Önerilen seviyeyi uygula" düğmesi).
**Karar:** Proje yokken kayıt `<proje_kökü>/Hizli_Kayitlar/02_Audio_WAV/` altına gider; proje açıkken `<proje>/02_Audio_WAV/`.
**Karar:** İlk açılışta teleprompter'a düzenlenebilir örnek metin konur (tuvaldeki metin); kullanıcı metni ayarlara kaydedilir.
**Karar:** FFmpeg 24-bit WAV'ı `WAVEX` başlığıyla yazar (geçerli `.wav`, her oynatıcı okur); uzantı ve içerik yalnız WAV.
**Karar:** `selftest` loopback (127.0.0.1) bağlantılarını "ağa çıkma" saymaz (Ollama/ComfyUI yerel algılama, Haiku Adım 5); internet dışı.
**Karar:** Önceki adımda getirdiğim tema gerilemesini düzelttim: `ThemeManager.apply` ilk çağrıda QSS'i yüklemiyordu (yalnız mod değişince) → ilk açılış stilsizdi; regresyon testi eklendi.
**Test:** diğer şeritler (aynı koşuda): core 149 · security 165 · storage 97 · downloader 53 · settings 48 — hepsi yeşil
**Bilinen eksikler:** gerçek mikrofona açılan akış elle denenmedi (testler sahte akışla; yalnız aygıt *sorgusu* gerçek) · faster-whisper bu ortamda kurulu değil → gerçek transkripsiyon yalnız sahte modelle sınandı; model ilk kullanımda internet ister (sonra çevrimdışı) · zaman çizelgesine "ses ekle/Kurguya gönder" bağı yok (Adım 7) · mastering yalnız konuşma ön ayarı (başka ön ayar yok) · gerçek Wayland/xcb ve çok monitörlü teleprompter denenmedi

---
## Ayrıntılar (karşı thread okumaz)

Ekran görüntüleri (offscreen, sahte mikrofonla kayıt sırasında): [`step6/simple_recording.png`](step6/simple_recording.png) · [`step6/legend_recording.png`](step6/legend_recording.png)

### 1. Ne yapıldı
| Parça | Davranış |
|---|---|
| **Kayıt** `engine.py` | `sounddevice.InputStream` geri çağrısı hafif (kopyala → kazanç → VU → kapı); disk yazımı ve LUFS/true-peak analizi ayrı **işçi iş parçacığında** (sınırlı kuyruk; dolarsa blok düşer, `dropped_blocks`/`xruns` sayılır, geri çağrı asla engellenmez). `soundfile` ile **PCM_24 `.wav`**, başlık ~1 sn'de bir güncellenir (çökmede dosya okunabilir). Basit 48 kHz sabit; Legend 44.1/48/88.2/96 kHz, mono/stereo. `.wav` dışı uzantı `ValueError`. Akış fabrikası enjekte edilebilir. |
| **Aygıt/hata** `devices.py` | PortAudio tembel içe aktarılır; backend/aygıt yoksa (Docker/CI) `SmartError` (`audio.no_backend/no_input/device_busy/unsupported_rate/unsupported_channels/permission/open_failed`), çökme yok. Yazma hatası (disk dolu) kaydı düzgünce kapatır, dosya geçerli kalır. |
| **DSP** `dsp.py` | Gerçek zamanlı **gürültü kapısı** (eşik/atak/tutma/bırakma/aralık + histerezis; 2 ms alt blok, doğrusal kazanç rampası, blok boyutundan bağımsız); **VU** (kanal başına tepe/RMS/tepe tutma, clipping mandalı); **BS.1770-4 K-ağırlıklı LUFS-M/S/I + 4× true peak** (numpy-only FIR/FFT; −20 dBFS mono 1 kHz → −23.00 LUFS, tam ölçek → −3.01, 44.1–96 kHz'te ±0.02); **`analyze_level`** (konuşma seviyesi, gürültü tabanı, tepe tavanı, hüküm sessiz/kısık/iyi/yüksek/clipping, −18 dBFS hedef). |
| **Teleprompter** `teleprompter.py` | Kelime tabanlı ilerleme (Yavaş 110 / Normal 140 / Hızlı 170, serbest 40–300 WPM), yazı boyutu, ayna, kayıtla senkron başlat; durum servisin `Prompter`'ında, görünüm `PrompterView` yalnız çizer. |
| **Yazıya çevirme** `whisper_local.py` + `srt.py` | faster-whisper isteğe bağlı: yoksa **"İsteğe bağlı paketi kur"** eylemli `SmartError` (komutu panoya kopyalar); model önce yerel önbellekten aranır, yoksa indirilir (internet yoksa `SmartError`); iptal segmentler arasında; çıktı atomik `.srt` (UTF-8, ≤42 kr/satır). İş kuyruğunda, kullanıcı tetikler. |
| **Mastering** `mastering.py` | FFmpeg: HP 80 Hz → EQ (−2 dB@200, +2@3k, raf +1.5@9k) → kompresör 3:1 → de-esser → `loudnorm` iki geçiş doğrusal (**−14 LUFS**, TP −1) → limiter. **Yeni dosya** `*_master.wav` (var olanın üzerine yazmaz), orijinal bayt-bayt aynı; iptalde yarım dosya silinir; FFmpeg yoksa/başarısızsa Haiku'nun hata sözlüğüyle `SmartError`. Gerçek denemede −32.2 → −13.99 LUFS, TP −1.03 dBTP. |
| **Servis** `service.py` | Ayarlar (`ctx.settings["recorder.*"]`), aygıt seçimi, kayıt/mikrofon testi durum makinesi, ~30 fps `ticked` (yalnız kayıt/test/teleprompter sürerken), kayıt listesi (`02_Audio_WAV` taraması + arka planda dalga formu önbelleği), iş işleyicileri, kapanışta süren kaydı bitirme, mod geçişinde kaydın sürmesi. |
| **Basit sayfa** | Tuvaldeki "Ses Kaydet": büyük pembe kayıt düğmesi (kayıtta durdur karesi + nabız halkası) + dev süre, pastel 20 segmentli VU (nane→tereyağı→şeftali→pembe) + canlı "Sesin harika!" çipi, "Arka plandaki vızıltıyı sustur" anahtarı + durum cümlesi, "Mikrofonu Dene", kahverengi okuma ekranı (geçmiş soluk, okunan satır tereyağı), hız hapları, "Konuşmayı Yazıya Çevir (.srt)", kayıt listesi (dalga formu, Yazıya çevir / Stüdyo sesi yap). Dar pencerede tek sütun. |
| **Legend sayfa** | `RECORDER · WAV 48 kHz / 24-bit` + aygıt/hız/kanal seçimi, mono zaman kodu + REC ışığı, dikey stereo metre (dB ölçeği, tepe tutma, CLIP), LUFS-M/LUFS-S/TRUE PEAK kutuları, NOISE GATE paneli (OPEN/HOLD/CLOSED/BYPASS ışığı, 5 parametre, GR çubuğu), Auto-Gain kalibrasyonu (−18 dBFS), siyah teleprompter (WPM, yazı, ayna, senkron), take tablosu (dalga, .srt, stüdyo), dil/model/hedef LUFS + eylemler. |

### 2. Şerit dışı dokunuşlar
1. `ui/theme/manager.py` + `tests/core/test_theme.py`: Adım 3'te `apply()`'ı "değişiklik yoksa yeniden kurma" yaptığımda **ilk yüklemeyi** de atlıyordu (QSS yalnızca mod değişince yükleniyordu). `_installed` ile düzeltildi; iki regresyon testi (`create_app` sonrası stil, tekrar çağrıda tek yayın) eklendi. `test_qss_uses_the_tokens` `qapp`'a bağlandı (sıra bağımlılığı).
2. `tests/core/test_i18n.py`: koddaki her anahtar kontrolü artık `kadraj/modules/*/strings.py` dizelerini de sayar (modül dizeleri çekirdek tabloda değildir).
3. `tests/core/test_shell.py::test_simple_home_has_task_cards_and_soon_placeholders`: sabit liste yok; her kartın `soon` değeri "modül kayıt defterinde yok" ile eşit, yer tutucu/gerçek sayfa kontrolü genel.
4. `conftest.py` + `ThemeManager._install`: ilk QSS yüklemesi düzelince her test bağlamı stili yeniden yüklüyor ve birikmiş widget'ları yeniden boyuyordu (recorder lane'i 108 sn). `_install` aynı QSS/yazı tipi zaten yüklüyse atlar; kök `conftest.py` her testten sonra `deleteLater` bekleyen widget'ları siler → lane ≈19 sn, `tests/core` ≈23 sn.
5. `main.py`: selftest'e ses kaydı (sahte akış, LUFS), aygıt sorgusu ve recorder kaydı eklendi; loopback bağlantıları sayılmaz.

### 3. Test kapsamı (`tests/recorder`, gerçek mikrofon ve ağ yok)
`test_dsp` (kapı davranışları, VU, LUFS referansları 4 örnekleme hızında, true peak, auto-gain) · `test_engine` (PCM_24 tam kare sayısı, 24-bit doğruluk, `.wav` dışı uzantı reddi, canlı kazanç, kapı dosyayı şekillendirir, taşma düşürür/engellemez, disk hatası, aygıt yok) · `test_devices` (PortAudio hata eşlemesi, backend yok) · `test_teleprompter` · `test_srt` · `test_whisper` (sahte model, iptal, çevrimdışı model, eksik paket+eylem) · `test_mastering` (gerçek FFmpeg −14±0.8 LUFS, orijinal korunur, iptal, sahte süreç komut satırları) · `test_takes` · `test_service` (mod kuralları, hata yolları, mikrofon testi, işler, kapanış, kayıt mod geçişinde sürer) · `test_widgets` · `test_pages` (iki sayfa, kabuk entegrasyonu, ağ engelli akış) · `test_module` (dizeler TR/EN eşit, kullanılan her anahtar var, marka kuralı, hafif içe aktarma, `.wav`-only).

### 4. Dosya listesi
```
kadraj/modules/recorder/{__init__,module,service,engine,dsp,devices,takes,teleprompter,srt,mastering,widgets,
                         page_base,page_simple,page_legend,strings}.py  requirements.txt
kadraj/ai_engine/providers/whisper_local.py
tests/recorder/{conftest,test_dsp,test_engine,test_devices,test_teleprompter,test_srt,test_whisper,test_mastering,
                test_takes,test_service,test_widgets,test_pages,test_module}.py
docs/steps/step6/{simple_recording,legend_recording}.png
değişen: ui/theme/manager.py  tests/core/{test_theme,test_i18n,test_shell}.py  main.py
```
