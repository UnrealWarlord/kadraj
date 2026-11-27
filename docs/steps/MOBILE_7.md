# Mobil 7. adım · CI: Android APK'sı (derleme, imza, Release) ve emülatörde açılış denemesi

Tarih: 2026-11-27 · Dal: `mobile` · Ortam: Claude'un bulut makinesinde Docker (`kadraj-android-build`, `kadraj-test`;
M8). GitHub Actions burada çalışmaz: iş akışının çalıştırdığı betikler bulut makinesinde sıfır önbellekle baştan sona
çalıştırıldı. Actions'taki ilk gerçek çalıştırma push'tan sonra (M7: push Erol'da, Actions Claude'da).

## Sonuç

### 7a · Tek komutla APK: `packaging/build_android.sh` (M26)

- `bash packaging/build_android.sh [release|debug]` — gereken tek şey Docker (Linux paketindeki `build_linux.sh` gibi):
  1. derleme imajı (`packaging/android/Dockerfile`; bağlamsız derlenir, büyük klasörler gönderilmez),
  2. Android NDK r28c — `dl.google.com`'dan, **sabit SHA-256** ile doğrulanarak (PySide6 6.12'nin kendi indirdiği
     sürüm, `Pkg.Revision 28.2.13676358`),
  3. yerel araçlar (FFmpeg, ffprobe, QuickJS; `build_native.sh`) — derleme betiği, imaj ya da NDK değişmedikçe
     önbellekten (`build/android/native/.stamp`),
  4. APK (`build_apk.sh`).
- Çıktı (`dist/`): `KADRAJ-<sürüm>-android-arm64-unsigned.apk` (+ `.sha256`) ve
  `KADRAJ-<sürüm>-android-native-sources.tar` (+ `.sha256`; APK'daki yerel araçların tam kaynağı + derleme betiği,
  GPL karşılığı kaynak — `THIRD_PARTY_NOTICES.md` bu adı söylüyor).
- Önbellek `build/android/` (`KADRAJ_ANDROID_CACHE` ile değişir): `ndk/`, `native/`, `home/` (Android SDK, Gradle),
  `apk-work/` (buildozer), `wheels/` (PySide6 Android tekerlekleri).
- Konteyner imajdaki `builder` kullanıcısıyla (uid 1000) çalışır; başka uid'li makinede (GitHub çalıştırıcısı: 1001)
  bağlanan klasörler yazılabilir yapılır.
- Vekil sunucu arkasında derlemek için: `KADRAJ_DOCKER_BUILD_ARGS` (Dockerfile'daki CA gizlileri), `KADRAJ_DOCKER_ARGS`,
  `KADRAJ_DOCKER_ENV_FILE`. CI'da hiçbiri gerekmez.

### 7b · İmza: `bash packaging/build_android.sh sign` → `packaging/android/sign_apk.sh` (M26)

- İmzasız release APK'sı **ağsız** bir konteynerde, çalıştıranın kullanıcısıyla imzalanır (anahtar dosyası `600`
  kipinde kalabilir). Araçlar: derlemede buildozer'ın indirdiği SDK'daki build-tools (37.0.0).
- Sıra: `zipalign -P 16` (yerel kütüphaneler 16 KB sayfa sınırına; Android 15+ 16 KB sayfalı cihazlar) →
  `apksigner sign` (**v2 + v3**; v1 kapalı, en düşük sürüm Android 9) → `apksigner verify` (v2 ve v3 ayrı ayrı
  denetlenir) → yeniden hiza denetimi → `.sha256`. Sonra imzasız dosya silinir:
  `dist/KADRAJ-<sürüm>-android-arm64.apk` (+ `.sha256`).
- Parolalar apksigner'a değişken adıyla gider (`--ks-pass env:…`), konteynere de yalnız adla (`-e AD`); komut
  satırında ve günlükte görünmez. Günlüğe sertifikanın sahibi (DN) ve SHA-256 parmak izi yazılır — ikisi de APK'nın
  içinde zaten herkese açık. Yanlış parola ya da anahtar adı sade bir hata verir.
- Ortam: `KADRAJ_KEYSTORE` (dosya), `KADRAJ_KEYSTORE_PASSWORD`, `KADRAJ_KEY_ALIAS`, `KADRAJ_KEY_PASSWORD` (boşsa
  deponunki; PKCS12'de ikisi aynıdır). PKCS12 ve JKS depoları denendi.

### 7c · `release.yml` → `android` işi (M26, M27)

Sırasıyla:

1. **Sürüm kapısı** (yalnız release): etiket `v` + `kadraj/version.py` değilse derlemeden önce düşer (Linux ve
   Windows işleriyle aynı).
2. **İmza kapısı:** dört gizlinin yalnız *var olup olmadığına* bakar (`secrets.X != ''`; değerler bu adıma girmez).
   Eksikse release'te iş düşer; elle denemede uyarı verir ve imzasız APK üretir (kurulamaz, yalnız derleme denetimi).
3. **Disk yeri:** çalıştırıcıdaki kullanılmayan araçlar silinir (.NET, Android SDK, GHC, CodeQL); derleme ~15 GB ister.
4. **Yerel araç önbelleği** (`actions/cache@v6`, `build/android/native`): anahtar `build_native.sh`, `Dockerfile` ve
   `build_android.sh`'nin özeti — betiğin kendi damgasıyla aynı dosyalar. Etiket çalıştırması varsayılan dalın
   önbelleğini kullanabilir; önce `main`'de bir elle deneme release'i kısaltır.
5. **Derleme:** `bash packaging/build_android.sh release`.
6. **İmza** (gizliler varsa): base64'ten çözülen anahtar `$RUNNER_TEMP`'te yalnız çalıştırıcının okuyabileceği kipte
   durur, adım bitince silinir; geçersiz base64 sade bir hata verir. Gizlilerin değeri yalnız bu adıma girer.
7. **Artifacts** (`KADRAJ-android-arm64`, 30 gün) ve **Release'e ekle** (`gh release upload --clobber`): imzalı APK,
   kaynak arşivi ve ikisinin `.sha256`'sı.

Zaman sınırı 180 dk (ilk çalıştırma yerel araçları da derler).

### 7d · `release.yml` → `android-smoke` işi: emülatörde açılış (M28, bilgi amaçlı)

- `packaging/android/smoke_emulator.sh`: GitHub'ın KVM'li Linux çalıştırıcısında Android 11 x86_64 emülatörü
  (`system-images;android-30;google_apis;x86_64`, ARM64 çevirisiyle) kurar, APK'yı kurar (`-g`: izinler verilmiş) ve
  iki kez açar: **telefon** (Pixel 4, en küçük genişlik ~393 dp) ve **tablet** (`wm size 1600x2560`, `wm density 320`
  → 800 dp).
- Uygulama Android'de açılış aşamalarını logcat'e yazar (`kadraj/crashlog.py` → `note`, `kadraj/ui/shell/app.py`):
  `KADRAJ: açılış: phone|tablet, en küçük genişlik N dp`, `onay soruluyor`, `sihirbaz açıldı`,
  `ana pencere açıldı (simple)`. Deneme onayı Enter ile kabul eder (varsayılan düğme), sihirbazı Esc ile atlar, her
  aşamada ekran görüntüsü alır.
- Geçer sayılması için iki boyutta da: ana pencere açılmış, süreç çalışıyor, logcat'te Python hatası / çökme yok,
  açılış satırı beklenen boyutu (telefon / tablet) söylüyor.
- Sonuç tablosu iş özetinde; ekran görüntüleri, logcat ve emülatör günlükleri `android-acilis-denemesi` artifact'ında.
  Uygulamanın hata günlüğü (`KADRAJ-hata.txt`) varsa o da alınır.
- İş düzeyinde `continue-on-error: true`: sürüm paketini engellemez. KVM ya da ARM64 çevirisi yoksa kendini atlar.
  İmzasız APK (gizliler yokken) yalnız bu deneme için geçici bir anahtarla imzalanır.

## İmza anahtarı — Erol'un yapacakları (bir kez)

Claude anahtarı ve parolayı görmez; sohbete yazılmaz.

1. Java'nın `keytool`'u gerekir (Ubuntu: `sudo apt install openjdk-17-jre-headless`). Anahtar:
   `keytool -genkeypair -v -keystore kadraj-release.jks -alias kadraj -keyalg RSA -keysize 4096 -validity 10000`
   (parola ve ad/kurum sorar; Java 9+ PKCS12 depo üretir, anahtarın parolası deponunkiyle aynıdır).
2. **Yedek:** `kadraj-release.jks` ve parola iki ayrı güvenli yerde (çevrimdışı) saklanır. Anahtar kaybolursa telefona
   kurulu KADRAJ güncellenemez; kullanıcı kaldırıp yeniden kurmak zorunda kalır (Android aynı imzayı ister).
3. `base64 -w0 kadraj-release.jks` çıktısının tamamı kopyalanır.
4. GitHub → depo → Settings → Secrets and variables → Actions → New repository secret:
   `KADRAJ_KEYSTORE_B64` (3. adımın çıktısı), `KADRAJ_KEYSTORE_PASSWORD` (parola), `KADRAJ_KEY_ALIAS` (`kadraj`),
   `KADRAJ_KEY_PASSWORD` (aynı parola; boş bırakılabilir).
5. Actions → "Sürüm paketleri" → Run workflow ile bir elle deneme: `android` işinin Artifacts'ında imzalı APK olur;
   günlükteki "certificate SHA-256 digest" satırı anahtarın parmak izidir (README'ye yazılabilir).

## Plandan farklar

- Plan "seed → APK → imzala" diyordu: Android'de indirilip konan bileşen yok; FFmpeg/ffprobe/QuickJS kaynaktan
  derlenir (M13, M14) ve önbelleğe alınır, yt-dlp APK'ya Python paketi olarak girer.
- Plan imzayı doğrudan çalıştırıcıda düşünüyordu: imza ağsız bir konteynerde atılıyor (aynı araçlar yerelde ve CI'da;
  çalıştırıcının Android SDK'sına bağlı değil).
- Plana ek: Release'e APK'nın yanında yerel araçların kaynak arşivi de konuyor (GPL, M14).
- Plana ek: bilgi amaçlı emülatör denemesi (plan §7 "Doğrulama: KVM varsa emülatör"; bulut makinesinde KVM yok,
  GitHub çalıştırıcısında var).

## Bilinen sınırlar

- **`android-smoke` hiç çalıştırılamadı** (bulut makinesinde KVM yok): ilk gerçek çalıştırma GitHub'da. Android 11
  x86_64 imajındaki ARM64 çevirisi PySide6'yı açamayabilir ya da çok yavaş olabilir; o durumda iş "zaman aşımı" ya da
  "kapandı" der ve sürümü engellemez. Asıl doğrulama yine gerçek cihazda.
- Actions burada çalışmadığı için `release.yml` yalnız `actionlint` (+ shellcheck) ve dosya testleriyle denetlendi;
  `actions/cache@v6`, `download-artifact@v8` gibi sürümler etiketlerinden okundu.
- Depo private ise GitHub çalıştırıcısı 2 çekirdekli: ilk Android derlemesi uzun sürer (zaman sınırı 180 dk) ve
  Actions dakikası harcar. Önbellek etiket çalıştırmalarında ancak varsayılan daldan (main) gelir.
- Release çalıştırması imza gizlileri olmadan düşer (bilerek, M27).
- Claude'un GitHub bağlantısı bu depoyu görmüyor (yalnız başka bir depo listeleniyor): Actions'ı Claude'un
  çalıştırıp izleyebilmesi için Claude GitHub uygulamasının `kadraj` deposuna kurulması gerekir.

## Doğrulama

- **CI'ın aynısı, sıfır önbellekle** (bulut makinesi, 4 çekirdek; vekil sunucu ayarları `KADRAJ_DOCKER_*` ile):
  `bash packaging/build_android.sh release` **15 dk 35 sn**, çıkış 0. İmaj önbellekten geldi; NDK indirildi ve SHA-256
  tuttu; yerel araçlar sıfırdan derlendi; buildozer Android SDK'yı, Gradle bağımlılıklarını ve PySide6 tekerleklerini
  kendisi indirdi. Önbellek ~7,7 GB (NDK 2,2 GB, SDK + Gradle 1,7 GB, buildozer 3,6 GB, yerel araçlar 114 MB) + imaj
  2,2 GB. Derleme betiğinin denetimleri (köprü sınıfları, sağlayıcı, uyarlanır simge) geçti.
- **Önbellekli ikinci çalıştırma:** aynı komut **9 dk 52 sn**, çıkış 0; yerel araçlar önbellekten geldi ("yerel araçlar
  önbellekten"), kaynak arşivi önbellekteki kopyadan yazıldı, iki `.sha256` de tuttu. (CI'da yalnız yerel araçlar
  önbellekte; NDK, SDK ve Gradle her çalıştırmada yeniden iner.)
- Çıktılar: imzasız release APK'sı 86 MB; kaynak arşivi 66 MB (8 kaynak paketi + `build_native.sh` + `VERSIONS.txt`).
- `bash packaging/build_android.sh sign` (deneme anahtarı, PKCS12; 1,5 sn): v2 ve v3 doğrulandı, `zipalign -c -P 16`
  geçti, `sha256sum -c` iki dosyada da geçti, imzasız dosya silindi. `aapt2`: `minSdk 28`, `targetSdk 35`, hata
  ayıklama kapalı, izinler `INTERNET` + `RECORD_AUDIO`, `extractNativeLibs=true`, sağlayıcı `org.insibag.kadraj.files`.
  JKS depoyla da imzalandı; yanlış parola ve yanlış anahtar adı sade hata verdi. Deneme anahtarıyla imzalı APK
  dağıtılmadı (gerçek anahtarla gelen güncellemeyle çakışır).
- APK'nın içindeki (Gradle'ın soyduğu) FFmpeg / ffprobe / QuickJS: `packaging/android/test_native.sh` (qemu) hepsi
  geçti, uçtan uca indirme yolu dahil. Sıfırdan derlenen araçlar 3. adımdakilerle bit düzeyinde aynı değil (derleme
  klasörü farklı), sınamaların hepsini geçiyor.
- `actionlint` 1.7.12 (shellcheck ile) `release.yml`'de temiz; `shellcheck` üç yeni betikte temiz.
- Testler: `tests/core/test_android_ci.py` (7 yeni: dosya adları, gizlilerin yalnız imza adımına girmesi, parolanın
  komut satırına düşmemesi, önbellek anahtarı, sabit NDK, 16 KB hizası + v2/v3, emülatör denemesinin beklediği satırları
  uygulamanın yazması), açılış günlüğü (`test_mobile_android.py` +2, `test_crashlog.py` +1). `tests/core`: 321 geçti.

## Kararlar

- Karar: (M26) Android CI'ı Linux paketindeki gibi tek betik (`packaging/build_android.sh`): Docker'da sabit NDK →
  önbellekli yerel araçlar → APK; imza ağsız konteynerde, 16 KB hizalı, v2 + v3. Yayımlanan dosyalar
  `KADRAJ-<sürüm>-android-arm64.apk` ve `KADRAJ-<sürüm>-android-native-sources.tar` (+ `.sha256`).
- Karar: (M27) İmza gizlileri yoksa elle denemede imzasız APK + uyarı, release'te iş düşer.
- Karar: (M28) Emülatör denemesi bilgi amaçlı ayrı iş; uygulama Android'de açılış aşamalarını logcat'e yazar.
- Karar: (M6) CI depo görünürlüğünden bağımsız; görünürlük Erol'un GitHub ayarı olarak kalır. APK'yı başkaları da
  Releases'ten indirecekse depo public olmalı.

## DEVİR

8. adım (README + sürüm notu + son doğrulama) için:

- README'nin indirme tablosuna `KADRAJ-<sürüm>-android-arm64.apk` (telefon + tablet, Android 9+) eklenecek; dosya
  adları `release.yml`'in başındaki listeyle aynı kalmalı. Kurulum: "bilinmeyen uygulamaları yükle" izni, Android
  geliştirici doğrulaması (2027) ve gelişmiş akış notu (`docs/MOBILE_PLAN.md` §4), APK'yı doğrulama (`.sha256`,
  sertifika parmak izi).
- Sürüm notu `docs/release-notes/` altında; Android'de olmayan özellikler (M21) ve bilinen sınırlar (MOBILE_3–7) özetlenir.
- Push'tan sonra: Actions → "Sürüm paketleri" → Run workflow (main) — önbellek oluşur, `android-smoke` ilk kez çalışır;
  sonra `v3.0.0` release'i.
