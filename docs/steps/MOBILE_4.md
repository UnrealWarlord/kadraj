# Mobil 4. adım · Ses kaydı (Android): QtMultimedia, mikrofon izni, soundfile'sız .wav

Tarih: 2026-11-26 · Dal: `mobile` · Ortam: Claude'un bulut makinesinde Docker (`kadraj-test`, gerçek Qt ses yolu
için `kadraj-pulse`, APK için `kadraj-android-build`; M8). KVM yok: emülatör çalışmaz, cihaz denemesi Erol'da.

## Sonuç

- **Ses girişi (`kadraj/modules/recorder/qt_audio.py`, yeni):** Android'de `sounddevice`/PortAudio yok. Aynı üç işlev
  (aygıt listesi, ayar denetimi, akış fabrikası) QtMultimedia ile: `QMediaDevices.audioInputs()`, biçim seçimi
  (Float → Int16 → Int32 → UInt8, aygıtın desteklediği ilki), `QAudioSource` çekme (pull) kipinde; `readyRead`
  geldikçe baytlar `float32 (kare, kanal)` bloğa çevrilip kayıt motoruna verilir. Qt 6.12 Android'de AAudio kullanır.
  Kayıt motoru (`engine.py`), VU, gürültü kapısı, LUFS ölçümü ve mikrofon testi değişmedi; kayıt biterken Qt
  arabelleğinde kalan ses de yazılır (`drain`).
- **Seçim (`devices.audio_backend()`):** Android'de `qt`, masaüstünde `portaudio`; `KADRAJ_AUDIO_BACKEND=qt|portaudio`
  zorlar. Hata kodları aynı kaldı (`audio.no_backend`, `audio.permission`…); Android'de metinler telefona göre
  ("libportaudio2 kur" yerine "uygulamayı yeniden aç/kur"; izin için "Ayarlar → Uygulamalar → bu uygulama → İzinler →
  Mikrofon").
- **Mikrofon izni (`kadraj/modules/recorder/permission.py`, yeni):** `RECORD_AUDIO` APK bildiriminde
  (`build_apk.sh`). Çalışma zamanında Qt'nin `QMicrophonePermission`'ı ile, kayıt ya da mikrofon testi başlamadan
  sorulur. İzin penceresi açıkken servis "idle" kalır (düğmeye yeniden basmak ikinci pencere açmaz); izin gelince
  istenen iş kendiliğinden başlar; verilmezse telefon ayarlarını anlatan hata kartı çıkar. "Bir daha sorma"
  seçilmişse Android pencereyi göstermez, kart hemen gösterilir. Masaüstünde izin sorusu yok, davranış aynı.
- **.wav (`kadraj/modules/recorder/wavio.py`, yeni):** Android'de libsndfile (`soundfile`) yok. Yazma: standart
  kütüphaneyle PCM 24 bit WAV; başlık ~1 sn'de bir güncellenir (uygulama kapanırsa dosya okunabilir kalır), tek
  uzunluklu veri için RIFF dolgu baytı. Okuma (kayıt listesi, dalga formu, mastering): `wave` + numpy; 8/16/24/32 bit
  PCM ve WAVE_FORMAT_EXTENSIBLE (FFmpeg'in mastering çıktısı). Masaüstünde her şey eskisi gibi `soundfile` ile.
- **Kayıt yeri:** proje açıksa projenin `02_Audio_WAV` klasörü, değilse
  `Belgeler/KADRAJ_Projeler/Hizli_Kayitlar/02_Audio_WAV` (Android'de Belgeler = uygulamanın Documents klasörü).
  Kayıt sayfasındaki "Kayıt yeri" çubuğu tam yolu gösterir; telefon bilgisayara bağlanınca
  `Android/data/org.insibag.kadraj/files/Documents/…` altında görünür.
- Açılış denetimleri: Android'de ses satırı "Qt Multimedia" (PortAudio değil).

APK: `KADRAJ-2.0.0-android-arm64-debug.apk` (deneme; hata ayıklama imzalı) **89 MB** (açılmış 159 MB), sha256
`3015de85772fcd402d6e28a3a1ed665a7111fb763370f28fb2168571135a218c`. Bildirim dosyasında izinler: `INTERNET`,
`RECORD_AUDIO`. QtMultimedia ve Android ses eklentileri açılış listesinde; uygulama dosyalarında `qt_audio.py`,
`permission.py`, `wavio.py` var.

## Plandan farklar

- Plan "emülatörde `.wav` kaydı" diyordu; KVM olmadığından emülatör yok. Yerine gerçek `QAudioSource` yolu Linux'ta
  PulseAudio'nun sanal mikrofonuyla sınandı (boş çıkışın monitörü + 440 Hz ton): aygıt listesi, biçim seçimi, 48 ve
  44,1 kHz kayıt, frekans denetimi, mikrofon testi ve VU (`tests/recorder/test_qt_audio_live.py`;
  `KADRAJ_LIVE_AUDIO=sine:440` verilmezse atlanır). Android'deki arka uç AAudio, Qt API'si aynı. Telefonda deneme Erol'da.
- Plan `QMediaRecorder` ya da `QAudioSource` diyordu: `QAudioSource` seçildi (M18). Kayıt motorunun VU, gürültü kapısı,
  LUFS ve mikrofon testi PCM bloklarına ihtiyaç duyar; `QMediaRecorder` dosyayı kendisi yazar.

## Bilinen sınırlar

- Kayıt sürerken uygulama arka plana alınır ya da ekran kapanırsa Android mikrofonu kesebilir: kayıt durmaz ama
  yeni ses gelmez. 6. adımda "ekran açık kalsın" / ön plan bildirimi.
- "Klasörü aç", "Klasörde göster" ve "Oynat" Android'de dosya yöneticisini/oynatıcıyı açamayabilir (açılmazsa
  satırdaki not gösterilir, çökme yok); 6. adımda "Paylaş" / "Aç" ile değişecek.
- Android 11+ cihazlarda `Android/data/…` klasörü telefonun Dosyalar uygulamasında görünmeyebilir; bilgisayara USB
  ile bağlanınca görünür (6. adım: Paylaş / Galeriye kaydet).
- "Konuşmayı yazıya çevir" (faster-whisper) Android'de yok: kart "paket kurulu değil" der; 6. adımda telefonda gizlenir.
- Kapsayıcıdaki PulseAudio'da akışın ilk verisi ~2 sn sonra geldi (sunucunun açılışı). Telefonda AAudio ile bu süre
  çok kısadır; cihazda doğrulanacak.
- Qt 6.12'nin PulseAudio girişi `module-sine-source`'tan veri almadı (parecord alıyor); sınama bu yüzden monitör
  kaynağıyla yapıldı. Yalnız masaüstü Linux sınamasını ilgilendirir.

## Doğrulama

- `tests/recorder/test_android_audio.py` (yeni): 33/33 — sahte Qt kaynağı (yarım kare, boşaltma, açılış hatası),
  biçim seçimi, aygıt listesi, Android hata metinleri, izin akışı (bekle/ver/verme/test), soundfile'sız `.wav`
  yazma-okuma (soundfile ile karşılaştırmalı, PCM 8/16/24/32, WAVEX), motor, kayıt listesi, dalga formu, FFmpeg mastering.
- `tests/recorder/test_qt_audio_live.py` (yeni, PulseAudio sanal mikrofon): 4/4.
- Tam takım (Docker, ağ kapalı): 1178 geçti, 4 atlandı (canlı ses testleri; ses aygıtı yok). `--selftest`: TAMAM.

## Kararlar

- Karar: Android'de ses girişi QtMultimedia `QAudioSource` (çekme kipi); kayıt motoru, DSP ve arayüz aynı. `.wav`,
  `soundfile` yoksa standart kütüphaneyle PCM 24 bit. Mikrofon izni kayıt/test anında sorulur, izin gelince iş
  kendiliğinden başlar; `RECORD_AUDIO` bildirimde (M18).

## DEVİR

5. adım (anahtar kasası) için:

- Kasa (`kadraj/storage/vault.py`) `cryptography` (Fernet, PBKDF2HMAC) ve `keyring` kullanır; ikisi de APK'da yok.
  M4 gereği Android'de mod `passphrase` (PBKDF2-HMAC-SHA256, 600 000 yineleme). PBKDF2 standart kütüphanede var
  (`hashlib.pbkdf2_hmac`); Fernet için AES-128-CBC gerekir (standart kütüphanede yok). Seçenekler: p4a'nın
  `cryptography` tarifi (Rust araç zinciri ister) ya da Fernet biçimiyle uyumlu küçük bir saf Python uygulaması
  (kasa küçük, hız sorun değil). Dosya biçimi masaüstüyle aynı kalmalı (kasa taşınabilir).
- APK derlemesi: `packaging/android/build_apk.sh debug` (~20 dk; p4a dağıtımı her seferinde baştan kurulur).
- Ses kaydı Android'de `KADRAJ_AUDIO_BACKEND` olmadan kendiliğinden Qt yolunu seçer; masaüstünde Linux'ta Qt yolunu
  denemek için `KADRAJ_AUDIO_BACKEND=qt`.
