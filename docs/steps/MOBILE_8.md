# Mobil 8. adım · README, sürüm notu (3.0.0), son doğrulama ve `main`'e birleştirme

Tarih: 2026-11-27 · Dal: `mobile`, sonra yerel `main` · Ortam: Claude'un bulut makinesinde Docker (`kadraj-test`,
`kadraj-android-build`; M8). Push Erol'da, Actions Claude'da (M7).

## Sonuç

### 8a · README

- Özellikler: "Windows, Linux ve Android" satırı (telefonda Basit Mod, tablette iki mod; aynı APK).
- İndirme tablosu: `KADRAJ-<sürüm>-android-arm64.apk` satırı (Android 9+, telefon ve tablet, `.sha256`) ve
  `KADRAJ-<sürüm>-android-native-sources.tar`'ın kurulum için gerekmediği notu. Adlar `release.yml`'in başındaki
  listeyle aynı.
- Yeni "Android (telefon ve tablet)" bölümü: bilinmeyen uygulamaları yükleme izni, Play Protect ("Ayrıntılar" →
  "Yine de yükle"), telefon/tablet farkı, Belgeler klasörü, "Paylaş" / "Galeriye kaydet", Android'de olmayanlar (M21),
  güncelleme ve kaldırma uyarısı, imza (aynı sertifika, "mevcut paketle çakışıyor" ne demek, `apksigner verify
  --print-certs`), geliştirici doğrulaması (2027) ve gelişmiş akış / `adb install` (`docs/MOBILE_PLAN.md` §4).
- Gereksinimler: Android 9 (API 28)+, `arm64-v8a`, APK ~90 MB / kurulunca ~300 MB, FFmpeg/ffprobe/QuickJS APK'da,
  açılışta yalnız yt-dlp güncellenir.
- Kaynaktan derleme: `build_linux.sh` ve `build_android.sh`. Gizlilik: Android'de kasa parolayla açılır; açılış
  güncellemesi Android'de yalnız yt-dlp.
- Eski "Render için FFmpeg gerekir: `winget install Gyan.FFmpeg`" satırı kaldırıldı (FFmpeg 1.0.4'ten beri pakette).

### 8b · Sürüm notu `docs/release-notes/v3.0.0.md`

2.0.0'ın biçiminde: öne çıkanlar (Android, telefon/tablet, Paylaş / Galeriye kaydet, ses kaydı, parolalı kasa,
yalnız yt-dlp güncellemesi), Windows ve Linux'ta değişenler (kasa parola düğmeleri, hata kartı düğmeleri), dosya
tablosu, kurulum (Windows, Linux, Android), gereksinimler, bilinen sınırlar. "Sürüm 3.0.0" commit'ine girer
(`kadraj/version.py` ile birlikte, 2.0.0'daki gibi). GitHub'da release oluşturulurken metin olarak yapıştırılır.

### 8c · Birleştirme (M7, M29)

- Yerel `main` = `origin/main` (`61fc7b3`, `mobile`'ın çıktığı yer) → `git merge --no-ff mobile` → "Sürüm 3.0.0"
  (`kadraj/version.py` → `3.0.0`, sürüm notu). `mobile` dalı silinmedi.
- Erol'un yapacakları ve Actions sırası: aşağıda.

### 8d · Son doğrulamada denetlenen şüphe

- Şüphe: indirme hatası kartındaki **"Bileşenleri güncelle"** (`components.update`), o oturumda Ayarlar sayfası hiç
  açılmamışsa yanıtsız kalır mı (konuyu Ayarlar servisi dinliyor, servisler sayfayla başlıyor)? Denendi: ana pencere
  kurulurken bütün sayfalar, dolayısıyla bütün servisler başlıyor (masaüstü, telefon ve tablet). Düğme çalışıyor; kod
  değişmedi. Davranışı koruyan test eklendi:
  `tests/settings/test_service.py::test_error_card_update_button_works_before_settings_was_opened`.

## Plandan farklar

- Plan "Docker'da tam derleme + emülatör sınaması" diyordu: tam derleme CI'ın aynısıyla iki kez yapıldı (7. adım:
  sıfırdan ve önbellekle). Emülatör bulut makinesinde çalışmıyor (KVM yok): ilk emülatör denemesi push'tan sonra
  GitHub'da `android-smoke` işinde, gerçek cihaz denemesi Erol'da.
- Sertifika parmak izi README'ye yazılmadı: imza anahtarı henüz yok (Erol üretir, Claude görmez). İmza adımının
  günlüğünde `certificate SHA-256 digest` satırı olarak yazar; README'de `apksigner verify --print-certs` anlatıldı.

## Bilinen sınırlar (3.0.0'a giren)

Android:

- Uygulama gerçek bir cihazda henüz açılmadı (emülatör de yok). Araçlar qemu ile, telefon/tablet düzeni masaüstünde
  taklitle, APK'nın yapısı `aapt2` ve derleme betiğinin denetimleriyle sınandı. Sürüm notunda açıkça yazıyor.
- Java köprüsü ve dosya sağlayıcı ("Aç", "Paylaş", "Galeriye kaydet", ekran açık, geri tuşu) cihazda denenmedi;
  köprü `FileObserver` olaylarına dayanır (MOBILE_6).
- "Galeriye kaydet" yalnız Android 11+; Android 9–10'da "Paylaş" (MOBILE_6).
- `Belgeler/Imports`'taki kopyalar kendiliğinden silinmez (MOBILE_6).
- Tablet dikeyde Legend sayfaları ekrandan genişse yana kayar (MOBILE_6).
- Kasa parolası her açılışta bir kez sorulur; PBKDF2 arayüz iş parçacığında (MOBILE_5).
- Dışa aktarma işlemciyle (MediaCodec yok); kayıt sırasında uygulama arka plana alınırsa mikrofon kesilebilir
  (MOBILE_3, MOBILE_4).
- Kivy'nin `presplash.jpg`'si APK'da ama kullanılmıyor (MOBILE_6).
- `android-smoke` hiç çalışmadı; release imza gizlileri olmadan düşer (M27); private depoda çalıştırıcı yavaş
  (MOBILE_7).

Masaüstünden kalan (v2.0.0'da da var, bu işte değişmedi):

- Ana ekrandaki bırakma alanının "Bilgisayardan Seç" düğmesi dosya seçtirmek yerine "Proje aç" klasör penceresini
  açıyor (`home.py`: `browseRequested` → `openFolderRequested`). Telefonda ve tablette bırakma alanı gizli.
- Açılıştaki özet satırı (`startup.summary_line`) "sürüm" sözcüğünü dil ayarından bağımsız Türkçe yazıyor.
- Legend düzenleyicisinde sekmeli panellerin eski sekme çubuğu ikinci kez görünüyor (MOBILE_6).
- Legend düzenleyici sayfasının `hideEvent`'i ayarlara yazar; pencereleri kapatmadan çıkan betiklerde (ör. ekran
  görüntüsü betikleri) Python kapanırken "Signal source has been deleted" uyarısı basılır. Uygulamanın normal
  kapanışında görülmez.

## Doğrulama

- **Tam test** (Docker `kadraj-test`, ağ kapalı, Python 3.12), iki yarı:
  `tests/settings tests/core tests/recorder tests/downloader` **780 geçti, 4 atlandı** (canlı ses aygıtı isteyenler);
  `tests/ai tests/ai_easy tests/editor tests/security tests/storage tests/ui_pixels.py` **515 geçti**. Toplam **1295
  geçti, 4 atlandı**, hata yok.
- Yeni test (8d) düzeltme olmadan da geçti (davranış zaten doğru); `tests/settings` + `tests/core` + yeni test:
  400 geçti.
- **Selftest:** masaüstünde, `KADRAJ_PLATFORM=android KADRAJ_FORM_FACTOR=phone` ve `…=tablet` taklidinde üçü de
  `SELFTEST TAMAM` (iki modda ana pencere, proje korunuyor, ağa çıkma girişimi 0).
- **CI'ın aynısı** (7. adım): sıfırdan 15 dk 35 sn, önbellekle 9 dk 52 sn, çıkış 0; imzasız release APK'sı 86 MB,
  kaynak arşivi 66 MB; deneme anahtarıyla imza v2 + v3 ve 16 KB hizası doğrulandı.
- Emülatör ve gerçek cihaz yok (KVM yok): ilk emülatör denemesi GitHub'daki `android-smoke` işinde.

## Sürüm sonrası düzeltmeler (2026-11-27)

- **Android açılışta kapanıyordu** (v3.0.0'ın APK'sı, GitHub'daki ilk `android-smoke`): Qt yükleyicisi açılış
  listesindeki (`libs.xml`) PySide6 bağlantı modüllerini (`QtWidgets.abi3.so`…) `nativeLibraryDir`'de arıyor; Android
  APK'dan yalnız `lib*.so` adlı dosyaları oraya açtığı için bulamayıp uygulamayı kapatıyordu. Düzeltme:
  `p4a_hook.py` (`patch_libs`) bu öğeleri listeden çıkarır (modüller site-packages'tan Python ile yüklenir);
  PySide6 tarifi bu modülleri yerel klasöre kopyalamaz; `build_apk.sh` APK'da `lib*.so` dışı yerel dosya ya da
  listede `lib` ile başlamayan öğe görürse durur. Test: `test_p4a_hook_drops_python_bindings_from_the_qt_startup_list`.
- **Windows testleri:** Android'e özgü 4 POSIX testi Windows'ta atlanır; kayıt sayfasının aygıt satırı kelime
  kaydırmalı (Windows çalıştırıcısındaki uzun mikrofon adı telefon genişliğini aşıyordu).

## Kararlar

- Karar: (M29) 3.0.0 birleştirmesi `main`'de `mobile`'ın `--no-ff` birleştirmesi + ayrı "Sürüm 3.0.0" commit'i
  (`kadraj/version.py` + sürüm notu, 2.0.0'daki gibi); `mobile` dalı silinmez. Sürüm notu Android'in gerçek cihazda
  denenmediğini açıkça söyler (Erol cihazda denedikten sonra o satırı silebilir).

## Erol'un yapacakları

1. Eşitleme (Claude yapar, Erol'un bilgisayarı açıkken): `dist/` klasörüne gelen bundle'dan `mobile` ve `main`
   dalları alınır. Kontrol: `git log --oneline -3 main` → "Sürüm 3.0.0".
2. Cihaz denemesi (önerilir, release'ten önce): telefonda ve tablette açılış, Paylaş / Galeriye kaydet, ses kaydı,
   video indirme. Deneme APK'sı imzası farklı olduğu için release APK'sından önce kaldırılır.
3. İmza anahtarı ve 4 depo gizlisi: `docs/steps/MOBILE_7.md` → "İmza anahtarı — Erol'un yapacakları".
4. Claude GitHub uygulamasını `kadraj` deposuna kurmak (Claude'un Actions'ı çalıştırıp izleyebilmesi için).
5. Push: `git push origin main` (`mobile` dalını da saklamak istersen `git push origin mobile`).
6. Claude: Actions → "Sürüm paketleri" → Run workflow (main): Linux, Windows, Android ve `android-smoke` ilk kez;
   yerel araç önbelleği oluşur. Sorun çıkarsa düzeltme `main`'e yeni commit olarak gelir.
7. Erol: GitHub'da `v3.0.0` etiketiyle release (metin: `docs/release-notes/v3.0.0.md`); iş akışı paketleri ekler.

## DEVİR

Mobil yol haritası bitti. Sonraki iş istenirse: cihaz denemesinin sonuçları (MOBILE_6 → Bilinen sınırlar: köprü ve
dosya sağlayıcı ilk bakılacak şeyler; `android-smoke`'un ekran görüntüleri), Android Keystore (kasa parolasını
sormamak için), MediaCodec ile donanımlı dışa aktarma, iOS (M5: kapsam dışı).
