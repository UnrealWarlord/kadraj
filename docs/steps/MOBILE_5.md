# Mobil 5. adım · Anahtar kasası (Android): parolayla kasa, saf Python Fernet, Ayarlar'da parola

Tarih: 2026-11-26 · Dal: `mobile` · Ortam: Claude'un bulut makinesinde Docker (`kadraj-test`, APK için
`kadraj-android-build`; M8). KVM yok: emülatör çalışmaz, cihaz denemesi Erol'da.

## Sonuç

- **Şifreleme (`kadraj/storage/fernet_lite.py`, yeni):** APK'da `cryptography` yok (Rust ile derlenen yerel paket),
  `keyring` da yok. Fernet belirtimi standart kütüphane ve saf Python AES-128-CBC ile uygulandı: aynı belirteç
  biçimi, HMAC-SHA256 sabit zamanlı karşılaştırma, rastgele IV (`secrets`). Kasa önce `cryptography`'yi dener,
  yoksa bunu kullanır (`Vault._crypto()`). Belirteçler iki yönde de `cryptography` ile birebir uyumlu: kasa dosyası
  telefon ile bilgisayar arasında aynıdır, taşınabilir.
- **Parola türetme:** PBKDF2-HMAC-SHA256 (600 000 yineleme) artık `hashlib.pbkdf2_hmac` ile; sonuç `cryptography`'nin
  `PBKDF2HMAC`'ı ile bayt bayt aynı (eski kasalar aynen açılır). APK'daki Python'da `_hashlib` (OpenSSL) var; türetme
  C hızında.
- **Kasa durumu (`SettingsService.vault_state()`):** kasa yokken sistem anahtar zinciri de yoksa `needs_passphrase`.
  Yeni servis işlevleri: `set_vault_passphrase(parola, tekrar)` (en az 8 karakter, iki alan aynı olmalı),
  `unlock_vault(parola)`, `reset_vault()`.
- **Ayarlar (Basit ve Legend, `kadraj/modules/settings/vault_ui.py`, yeni):** API kasası kartında duruma göre
  "Parola belirle" ya da "Kilidi aç" düğmesi. Parola penceresi iki maskeli alan (parola + tekrar) ve "unutursan
  anahtarlar açılamaz" notu gösterir. Anahtar eklerken kasa parola istiyorsa ya da kilitliyse önce o pencere açılır,
  sonra anahtar kaydedilir (bir kez yeniden dener). Parola yalnız bellekte durur; diske, ayarlara, log'a yazılmaz.
- **Parola unutulursa:** kilitli kasada "Parolamı unuttum", bozuk kasada "Kasayı sıfırla" düğmesi. Onay penceresi
  (varsayılan düğme "Vazgeç") sonrası eski dosya `vault.bin.bad` olarak yedeklenir, kasa boş başlar. Telefonda
  uygulamanın iç klasöründeki dosya elle silinemediği için bu yol gerekliydi.
- **Hata penceresi düğmeleri (kabuk):** ana penceredeki hata kartlarında "Ayarlar'ı aç", bağlantı açma ve
  "Kasayı sıfırla" düğmeleri artık çalışıyor (önceden yalnız "Kopyala" çalışıyordu). Kasa hata kartı
  `Topics.VAULT_RESET` yayınlar; kabuk onay sorup sıfırlar.
- Kasa hata metinleri cihaza göre ("Bu bilgisayarda" yerine "Bu cihazda") ve düğmenin yerini söylüyor
  ("Ayarlar → API kasası → Kilidi aç / Parola belirle").
- `--selftest`'e yeni satır: parola kipinde kasa, kilitle/aç ve saf Python Fernet'in `cryptography` ile uyumu.

APK: `KADRAJ-2.0.0-android-arm64-debug.apk` (deneme; hata ayıklama imzalı) **89 MB**, sha256
`3c0b65cee4032836546c41c38eb00f090e468d59c51d267eeec5bf0f7ab2d745`. İzinler değişmedi (`INTERNET`, `RECORD_AUDIO`);
uygulama dosyalarında `fernet_lite.py` ve `vault_ui.py` var, `cryptography`/`keyring` yok.

## Plandan farklar

- Plan "Android Keystore ya da parola yedeği" diyordu: önce parola yedeği (M4). Android Keystore (pyjnius) sonraya
  kaldı; kasa dosyası biçimi değişmeden eklenebilir (yeni bir `mode`).
- Plan bu adım için yalnız "Ayarlar'da anahtar girişi" diyordu. Masaüstünde de anahtar zinciri olmayan
  bilgisayarlarda (ör. Secret Service'siz Linux) parola kipini kuracak ya da açacak bir arayüz yoktu; aynı düğmeler
  masaüstünde de çalışır.

## Bilinen sınırlar

- Kasa parolası her açılışta bir kez sorulur (anahtar gerektiren ilk işte "Kasa kilitli" kartı → Ayarlar →
  "Kilidi aç"). Android Keystore gelince sorulmayacak.
- Parola türetme (PBKDF2, 600 000 yineleme) arayüz iş parçacığında çalışır; telefonda parola belirleme ve kilit
  açmada yarım ile bir saniyelik takılma olabilir. Cihazda ölçülecek.
- Saf Python AES zamanlama yan kanallarına karşı sertleştirilmemiştir; yalnız cihazdaki kasa dosyasını şifreler
  (ağ üzerinden saldırı yüzeyi yok). Masaüstünde `cryptography` kullanılmaya devam eder.
- Kasa sıfırlanırsa eski anahtarlar geri gelmez (parola olmadan çözülemez); `.bad` dosyası yalnız parola sonradan
  hatırlanırsa diye saklanır.

## Doğrulama

- `tests/security/test_fernet_lite.py` (yeni): AES S-kutusu, FIPS-197 (Ek B ve C.1), NIST SP 800-38A CBC vektörleri,
  Fernet belirtim vektörü, bozuk/değiştirilmiş belirteçler (`cryptography` ile aynı davranış), süre sınırı, anahtar
  ve tür denetimleri, `cryptography` ile iki yönlü uyum (0-1000 bayt), PBKDF2 eşitliği, `cryptography`'siz kasa ve
  telefon ↔ bilgisayar kasa taşıma.
- `tests/settings/test_vault_passphrase.py` (yeni, 14 test): servis durumları, parola belirleme/kilit açma/yanlış
  parola, masaüstünde parola sorulmaması, Basit ve Legend düğmeleri, ilk anahtarda parola, vazgeçme, farklı parolalar,
  kilitli kasaya kaydetme, maskeli alanlar, "Parolamı unuttum" ile sıfırlama (onaylı/onaysız), onay penceresinin
  varsayılanı "Vazgeç", hata kartındaki "Kasayı sıfırla" ve "Ayarlar'ı aç" düğmeleri.
- Tam takım (Docker, ağ kapalı): 1219 geçti, 4 atlandı (canlı ses testleri; ses aygıtı yok). `--selftest`: TAMAM.

## Kararlar

- Karar: Android'de kasa parola kipinde; Fernet `cryptography` yoksa saf Python (`fernet_lite`), PBKDF2 `hashlib` ile.
  Kasa dosyası biçimi aynı (telefon ↔ bilgisayar taşınabilir). Parola Ayarlar'da belirlenir/açılır; unutulursa onaylı
  sıfırlama. Android Keystore sonra (M19).

## DEVİR

6. adım (telefon ve tablet düzeni, paylaşım) için:

- Kasa düğmeleri (`vault_action`, `vault_reset`) Basit sayfada tek satırda; telefon genişliğinde sığıyor ama 6. adımın
  dokunmatik düzeninde gözden geçirilmeli.
- Mikrofon izni, kayıt yeri ve "Klasörü aç"/"Oynat" düğmeleri için 4. adımın "Bilinen sınırlar"ı geçerli (Paylaş/Aç).
- APK derlemesi: `packaging/android/build_apk.sh debug` (~20 dk).
