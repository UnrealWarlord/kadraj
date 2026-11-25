# KADRAJ · Koordinatör kararları

Kullanıcı, thread'lerden gelen karar sorularında önerilen seçeneğin koordinatör tarafından seçilmesini ve hepsinin en sonda kendisine bildirilmesini istedi. Thread'ler de kendi küçük kararlarını adım raporlarında "Karar:" satırıyla yazar; bunlar son raporda burada toplanır.

| # | Adım | Konu | Seçilen | Neden |
|---|---|---|---|---|
| 1 | 4–6 | Ana ekranda "yakında" rozetinin testi | Her kart için `soon == modül kayıtlı değil` (sabit liste yok) | Modüller eklendikçe test kendiliğinden doğru kalır, thread'ler birbirinin testini kırmaz |
| 2 | 5 | API anahtarı "göz" düğmesi | Tam anahtar asla gösterilmez; göz yalnız ilk 6 + … + son 4 karakteri açar | Kasa kuralı (yalnız maskeli gösterim) korunur, kullanıcı hangi anahtar olduğunu yine ayırt eder |
| 3 | 5 | FFmpeg "tek tık güncelle" | yt-dlp: iş kuyruğunda pip ile güncelleme · FFmpeg: sistem paket yöneticisi talimatı + sürüm kontrolü | FFmpeg Linux'ta sistem paketi; uygulamanın onu kendiliğinden değiştirmesi riskli |
| 5 | 8c | Sonnet'in açık bıraktığı analitik paneli | Legend Proje merkezine bağlandı (KPI'lar gerçek veriyle), işi Haiku yaptı | Tuvaldeki Legend ana ekranında maliyet/token analitiği var; küçük, kolay iş |
| 6 | son kontrol | Varsayılan dil | İlk açılışta Türkçe, sistem yerel ayarından bağımsız; İngilizce yalnız Ayarlar'dan | Temiz sistemde (Türkçe yerel ayar yokken) uygulama İngilizce açılıyordu; KADRAJ Türkçe öncelikli ürün |
| 7 | son kontrol | Selftest kota kontrolü | Metin yerine sayısal sonucu doğrular, selftest dili açıkça ayarlar | Dile bağlı kontrol temiz Docker'da yanlış alarm veriyordu |
| 8 | son kontrol | Legend İndirici ve Ayarlar kontrastı | Sayfalar Sonnet'in modülleri gibi tema rollerine bağlanacak + piksel tabanlı kontrast testi | Açık zemin üstünde açık yazı çıkıyordu (okunmuyor) |
| 4 | 8 | 8. adımın bölünmesi | 8a (Haiku): prompt adaptörleri, stok medya istemcileri, `.fountain`/`.pdf` dışa aktarım, maliyet/token analitiği · 8b (Sonnet): sağlayıcılar, Fotoğraf, Dizi/Film akışı, düğüm editörü, foley | Kullanıcının "kolay iş Haiku'ya, zor iş Sonnet'e" kuralı; Haiku boşta kalmaz, Sonnet'in yükü ve token harcaması azalır |

## Thread'lerin kendi kararları (adım raporlarından)

| Adım | Thread | Karar |
|---|---|---|
| 5 | Haiku | Legend ayar bölümleri ilk seçildiklerinde kurulur (açılışta yalnız "Arayüz"); kabuk testleri 74 sn → 32 sn |
| 5 | Haiku | Yerel AI algılama ve araç sürümü kontrolü sayfa açılışında bir kez çalışır; yenileme düğmesi her zaman |
| 6 | Sonnet | Basit'te gürültü kapısı yumuşak sabit ön ayarlı (−42 dB) ve varsayılan KAPALI; Legend'de 5 parametre |
| 6 | Sonnet | Auto-gain / mikrofon testi yalnız öneri üretir; kazanç kendiliğinden değişmez ("Önerilen seviyeyi uygula") |
| 6 | Sonnet | Proje yokken kayıtlar `<proje_kökü>/Hizli_Kayitlar/02_Audio_WAV/` altına |
| 6 | Sonnet | Teleprompter ilk açılışta tuvaldeki örnek metinle gelir; kullanıcı metni ayarlara kaydedilir |
| 6 | Sonnet | 24-bit WAV, FFmpeg'de `WAVEX` başlığıyla (geçerli `.wav`) |
| 6 | Sonnet | Selftest, yerel 127.0.0.1 bağlantılarını "ağa çıkma" saymaz (Ollama/ComfyUI algılama) |
| 6 | Sonnet | 3. adımda kendi getirdiği tema gerilemesini (ilk açılışta QSS yüklenmemesi) düzeltti, regresyon testi ekledi |
| 7 | Sonnet | Zaman içeride saniye (float), kare sınırına yuvarlanır; `HH:MM:SS:FF` yalnız gösterim/giriş (drop-frame yok); proje dosyası eklemeli, bilinmeyen alanları korur |
| 7 | Sonnet | Donanım kodlayıcı denenerek algılanır (NVENC → VAAPI → CPU); ProRes, kayıpsız ve WAV her zaman CPU |
| 7 | Sonnet | Podcast şablonu yalnız `.wav` (24-bit/48 kHz, −16 LUFS); Arşiv = ProRes 422 HQ `.mov`; WhatsApp ≤15,5 MB'a göre bit hızı |
| 7 | Sonnet | Önizleme düşük çözünürlüklü birleşik dosyadan QMediaPlayer ile; "Önce" karesi efektler çıkarılarak üretilir |
| 7 | Sonnet | Her otomasyon önce öneri sunar → kullanıcı "Uygula" → tek geri alma adımı; ağır paket yoksa "İsteğe bağlı paketi kur" kartı |
| 7 | Sonnet | Basit Mod 3 kanal, model sınırsız; Legend'de 4 çalışma alanı (Düzenle/Renk/Ses/Dışa Aktar), her biri ayrı düzen kaydı |
| 8a | Haiku | Kritik prompt alanları: konu, ışık, stil, oran; açı/lens/ruh hali/negatifler isteğe bağlı ama soruları listelenir |
| 8a | Haiku | Ortak stil kütüphanesi (8 stil) + model başına negatif/parametre dosyaları; stok sağlayıcılar için ortak `providers/stock.py` |
| 8a | Haiku | Kullanıcının Türkçe metni 8a'da çevrilmez (LLM yok); çeviri 8b'de LLM varsa yapılır |
