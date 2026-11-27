# Mobil 1. adım · Keşif: PySide6 merhaba-dünya APK'sı ve bağımlılık matrisi

Tarih: 2026-11-26 · Dal: `mobile` · Ortam: Claude'un bulut makinesinde Docker (M8; Erol'un bilgisayarı kullanılmadı).

## Sonuç

- **APK derlendi:** `kadrajmerhaba-0.1-arm64-v8a-debug.apk` (170 MB; açılmış hali 286 MB), Android Debug anahtarıyla (v2) imzalı. Depoya girmedi; proje dosyalarında
  `kadraj-android/KADRAJ-deneme-adim1.apk` olarak paylaşıldı (Erol telefona/tablete oradan kurar).
- **Çalıştırma:** Bulut makinesinde KVM yok, bu yüzden Android emülatörü açılamadı (x86_64 emülatörü donanım
  hızlandırması ister; arm64 imajları x86 üzerinde artık çalışmıyor). Deneme Erol'un telefonunda/tabletinde yapılacak.
  Uygulama açıldığında ekranda 2. adım için gereken bilgileri gösterir: `sys.platform`, API düzeyi, python-for-android
  ortam değişkenleri, yerel kütüphane klasörü, Qt standart klasörleri, ekran ölçüsü (dp) ve telefon/tablet tahmini.
- **APK içeriği (aapt2):** `targetSdk 35`, `native-code: arm64-v8a`, `extractNativeLibs=true` (yerel kütüphaneler
  diske açılır; 3. adımda `libffmpeg.so` bu sayede `nativeLibraryDir`'den çalıştırılabilir). İzinler: INTERNET,
  READ/WRITE_EXTERNAL_STORAGE. Boyutun çoğu Python paketi (`libpybundle.so`, 94 MB) ve Qt'nin bütün modülleri
  (Quick, Qml, Designer, Pdf… kullanılmasa da) ile QtMultimedia'nın FFmpeg kütüphaneleri (`libavcodec.so` vb.).
  Kullanılmayan Qt modülleri 7. adımda ayıklanacak.
- **Seçilen sürümler:** PySide6 6.12.0 (Android için `cp310-abi3` tekerlekleri), hedef CPython 3.14.2, NDK r28c,
  Android API 35 (derleme) / en düşük API 28 = Android 9 (M10), build-tools 37.0.0, Gradle 8.14.3, Android Gradle
  Plugin 8.11.0, JDK 17, python-for-android `58d21141` (v2026.05.09, PySide6 aracının sabitlediği), buildozer 1.6.0,
  Cython 0.29.33. Konak Python 3.12 (Ubuntu 24.04).

## Plandan farklar

- Planda "Android 8+" yazıyordu. PySide6 6.12'nin Android aracı en düşük API'yi **28 (Android 9)** olarak sabitliyor
  (Qt'nin alt sınırı). Plan ve DECISIONS (M10) güncellendi.
- Plan `qtpip download PySide6 --android` diyordu. `qtpip` Qt hesabı API'sine (`iapi.qt.io`) gidiyor; bunun yerine
  tekerlekler doğrudan `download.qt.io/official_releases/QtForPython/` altından indirildi ve yanlarındaki `.sha256`
  ile doğrulandı (ikisi de eşleşti).
- CPython artık python.org'un hazır Android derlemesinden değil, python-for-android'in kendi tarifinden derleniyor
  (konak için `hostpython3`, hedef için `python3`, 3.14.2).
- Plan bu modülü `kadraj/platform.py` diye anıyordu; standart kütüphanedeki `platform`'u gölgelememek için
  `kadraj/device.py` oldu (2. adım).

## Komutlar

```bash
# 1) İmaj (Dockerfile depoda: packaging/android/Dockerfile)
docker build -t kadraj-android-build packaging/android
docker volume create kadraj-android-sdk          # SDK/NDK, buildozer ve Gradle önbelleği (/home/builder)

# 2) Tekerlekler (sağlama doğrulamalı)
base=https://download.qt.io/official_releases/QtForPython
for w in pyside6/pyside6-6.12.0-6.12.0-cp310-abi3-android_aarch64.whl \
         shiboken6/shiboken6-6.12.0-6.12.0-cp310-abi3-android_aarch64.whl; do
  curl -LO "$base/$w"; echo "$(curl -sL "$base/$w.sha256")" | sha256sum -c -
done

# 3) Kapsayıcı içinde (uygulama klasöründe main.py olmalı)
docker run --rm -it -v kadraj-android-sdk:/home/builder -v "$PWD:/work" -w /work/hello kadraj-android-build bash
python "$(python -c 'import PySide6,pathlib;print(pathlib.Path(PySide6.__file__).parent/"scripts")')/android_deploy.py" \
  --name kadrajmerhaba --wheel-pyside ../pyside6-…aarch64.whl --wheel-shiboken ../shiboken6-…aarch64.whl -f -v
```

Araç NDK r28c'yi kendisi indirir (`~/.pyside6_android_deploy`), SDK'yı buildozer indirir (`~/.buildozer`).
Normal ağda tek komut yeterli. Bu oturumda ağ kısıtlı olduğu için iki ek iş yapıldı (aşağıda).

## Sorunlar ve çözümler

| Sorun | Çözüm |
|---|---|
| Bulut ortamı `dl.google.com`, `www.python.org`, `download.qt.io` ve Qt aynasını (`qt.mirror.constant.com`) engelliyordu | Erol'un onayıyla bulut ortamının izinli adres listesine eklendi |
| Vekil sunucu TLS'i yeniden imzalıyor; imajda apt ve Java bu CA'yı tanımıyor | Dockerfile'a isteğe bağlı BuildKit gizlileri eklendi: `bootstrap_ca` (apt'nin ilk HTTPS indirmesi) ve `extra_ca` (sisteme ve Java'ya eklenecek vekil CA). Verilmezse imaj olağan şekilde kurulur |
| apt'nin `_apt` kullanıcısı gizli dosyayı okuyamadı | CA önce `/tmp`'ye 0644 izinle kopyalanıp oradan kullanılıyor |
| GitHub arşiv indirmeleri (`codeload.github.com`) bu oturumda engelli (python3, hostpython3, libffi, sqlite3 tarifleri) | Kaynaklar `git clone --depth 1 --branch <etiket>` + `git archive --prefix=<ad>/` ile GitHub'ın arşiviyle aynı içerikte üretilip python-for-android'in indirme önbelleğine (`.buildozer/android/platform/build-arm64-v8a/packages/<tarif>/` + `.mark-<dosya>`) kondu. OpenSSL 3.3.1 GitHub sürüm dosyasından indirildi, resmî `.sha256` ile eşleşti. Normal ağda (CI) bu gerekmez |
| Araç başlarken projedeki `.buildozer` klasörünü siliyor (önbellek kayboluyor) | Önce `--init --keep-deployment-files` ile `buildozer.spec` ve PySide6/shiboken6 tarifleri üretildi, önbellek dolduruldu, sonra `python -m buildozer android debug` doğrudan çalıştırıldı |
| Gradle bağımlılıklarında Maven Central `429 Too Many Requests` | `~/.gradle/gradle.properties`: paralel indirme kapalı, en çok 2 işçi, 12 yeniden deneme ve 2 sn geri çekilme |
| Docker Hub ara sıra `429` (anonim çekme sınırı) | `ubuntu:24.04` bir kez `docker pull` ile çekilince derleme yerel imajı kullandı |

## Bağımlılık matrisi

Kaynak: python-for-android `58d21141` tarif listesi, paketlerin yapısı ve KADRAJ'da nerede kullanıldıkları.

| Paket | Android durumu | Etkilediği özellik | Plan |
|---|---|---|---|
| PySide6 | Qt'nin resmî Android tekerleği (6.12.0, abi3) | Tüm arayüz | ✓ Bu adımda doğrulandı |
| numpy | p4a tarifi (`numpy` v2.3.0, git'ten derlenir) | Düzenleyici (dalga biçimi, kapsamlar), ses DSP | ✓ Tarifle |
| Pillow | p4a tarifi (11.3.0; png, jpeg, freetype ile) | Fotoğraf, kapak, düzenleyici otomasyonları | ✓ Tarifle |
| cryptography | p4a tarifi (46.0.3; Rust ile derlenir, `static.rust-lang.org` ve crates.io erişilebilir) | Kasa (Fernet) | ✓ Tarifle (5. adım) |
| httpx (+httpcore, h11, anyio, sniffio, certifi, idna) | Saf Python (p4a'da tarif de var) | AI sağlayıcıları, bağlantı testi | ✓ |
| reportlab | 4.x saf Python; p4a'daki eski tarif `hg.reportlab.com`'dan C eklentili eski sürüm derliyor | `.pdf` senaryo dışa aktarımı | ✓ Tarif yerine PyPI'daki saf Python sürüm (7. adım) |
| yt-dlp, yt-dlp-ejs | Saf Python | Video indirme | ✓ JS çalıştırıcısı QuickJS ile (3. adım) |
| keyring | Saf Python ama Android arka ucu yok | Kasa anahtarının OS deposunda tutulması | Parola yedeği (M4) |
| sounddevice | PortAudio gerekir, Android'de yok | Mikrofon kaydı | QtMultimedia ile değiştirilecek (4. adım) |
| soundfile | libsndfile (cffi) gerekir, tarifi yok | `.wav` okuma/yazma | Android'de standart `wave` + numpy (4. adım) |
| psutil | C eklentisi, tarifi yok | Sistem izleme paneli | Android'de panel gizli (6. adım) |
| FFmpeg (ikili) | p4a'da kütüphane tarifi var (8.0.1) ama CLI değil; kaynak `ffmpeg.org` engelli, GitHub yansısı kullanılabilir | Düzenleyici, indirmede birleştirme | `libffmpeg.so` olarak gömülecek (3. adım) |
| faster-whisper | CTranslate2 (C++), tarifi yok | Yerel konuşma tanıma | Android'de kapalı; p4a'da `vosk` tarifi alternatif |
| librosa | numba/llvmlite, Android'de yok | Ses analizi (isteğe bağlı) | Android'de kapalı |
| opencv-python-headless | p4a'da `opencv` tarifi var (ağır) | İsteğe bağlı görüntü işleme | Android'de kapalı (gerekirse sonra) |
| rembg | onnxruntime, tarifi yok | Arka plan silme | Android'de kapalı |
| nvidia-ml-py | Android'de NVIDIA yok | GPU izleme | Android'de kapalı |
| pedalboard | JUCE (C++), tarifi yok | Ses efektleri (isteğe bağlı) | Android'de kapalı |

Kapalı özellikler masaüstündeki gibi "kurulu değil" kartıyla sade bir açıklama gösterir.

## Kararlar

- Karar: Android derleme ortamı Ubuntu 24.04 + JDK 17 + Python 3.12 venv içinde PySide6-Essentials 6.12.0,
  buildozer 1.6.0, Cython 0.29.33 (PySide6 aracının `default.spec`/`requirements-android.txt` değerleri).
- Karar: Derleme root olmayan `builder` kullanıcısıyla (buildozer root uyarısı vermesin); SDK/NDK ve önbellekler
  `kadraj-android-sdk` biriminde `/home/builder` altında, adımlar arasında saklanır (M8), 8. adımda silinir.
- Karar: Vekil sunucu desteği yalnız isteğe bağlı BuildKit gizlileriyle; CI ve normal bilgisayarlarda hiçbir etkisi yok.
- Karar: Kısıtlı ağda indirme önbelleğini doldurma işi depoya girmedi (yalnız bu oturuma özgü); CI tam ağla çalışır.
- Karar: Çalışma zamanı denemesi Erol'un cihazında; emülatör için KVM'li bir makine gerekir.
- Karar: Ayrı "matris APK'sı" (numpy, Pillow, cryptography…) bu adımda derlenmedi; aynı paketler tam uygulama
  APK'sında (6.–7. adım) derlenecek, böylece iki kez derleme yapılmıyor. Kısıtlı ağda gereken kaynaklar (Pillow,
  libjpeg-turbo, libpng, cffi, cryptography, FFmpeg) GitHub'dan aynı yöntemle hazırlanabilir; freetype'ın sürüm
  arşivi `download.savannah.gnu.org`'da ve o adres engelli, git'ten `autogen.sh` ile üretilecek.

## DEVİR

2. adım (platform katmanı) için:

- `kadraj/device.py`: `is_android()`, `form_factor()` (sw600dp eşiği), `android_private_dir()`,
  `native_library_dir()` (`/proc/self/maps`'ten; kütüphaneler APK içinden yükleniyorsa `None`).
- `paths.home_dir()` Android'de `<getFilesDir()>/.kadraj`; `prepare_android_environment()` `HOME`'u Qt'nin
  Documents klasörüne (uygulamaya ayrılmış dış depolama) çevirir, böylece `Path.home()` kullanan her yer çalışır.
- `components.py`: Android'de ffmpeg/ffprobe yalnız `nativeLibraryDir/lib<ad>.so`'dan; updater yalnız yt-dlp.
- Merhaba-dünya APK'sı cihazda açılınca ekrandaki bilgiler (özellikle `lib = …` satırı, `ANDROID_*` değişkenleri ve
  Qt Documents yolu) bu varsayımları doğrular; `lib` satırı `base.apk!` içeriyorsa 3. adımda
  `extractNativeLibs=true` / `useLegacyPackaging` gerekir.
