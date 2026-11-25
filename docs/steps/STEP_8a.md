# Adım 8a — AI motorunun kolay parçası (Haiku)

## DEVİR
**Yeni:** `kadraj/ai_engine/prompt_adapter/{__init__,base,midjourney,flux,imagen,dalle,sdxl}.py` + `data/{styles,midjourney,flux,imagen,dalle,sdxl}.json` · `kadraj/ai_engine/providers/{stock,pexels,pixabay,freesound}.py` · `kadraj/ai_engine/export/{__init__,fountain,pdf}.py` · `kadraj/ai_engine/analytics.py` · `tests/ai_easy/*` (6 dosya) · `docs/steps/STEP_8a.md`
**Değişmedi:** `providers/base.py`, `storage/db.py`, çekirdek, `kadraj/modules/editor/`
**Test:** `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -o addopts="" -q tests/ai_easy` → `51 passed` · tüm ağaç (dizin dizin): core `149` · storage `97` · security `165` · recorder `253` · downloader `53` · settings `48` · ai_easy `51` → `816 passed`
**Sonnet'in çağıracağı imzalar:**
- `PromptSpec.create(negatives="", subject=, lighting=, angle=, style=, aspect=, lens=, mood=, idea=)` → normalize; `missing_slots(spec) -> [MissingSlot(name, question, critical)]`
- `adapt(model, spec) -> AdaptedPrompt(model, text, negative, params)`; `model ∈ midjourney|flux|imagen|dalle|sdxl`; `PromptError` (Türkçe)
- `resolve_style`, `STYLE_IDS`, `ASPECTS`, `CRITICAL_SLOTS`; `dalle.size_for(aspect)`, `dalle.SIZE_OPTIONS`; `sdxl.RESOLUTIONS`
- `StockItem(provider, kind, id, title, page_url, preview_url, download_url, author, license, duration, width, height)`
- `PexelsProvider | PixabayProvider | FreesoundProvider(*, settings=None, transport=None).search(query, *, vault=None, timeout=8.0, limit=10, **filters) -> list[StockItem]`
- `providers.stock.install(registry, *, settings=None, transport=None)` → üçünü `replace=True` ile kaydeder
- Hatalar: `provider.no_key` · `vault.locked` (kasadan aynen) · `provider.auth|not_found|rate_limited|server|http` · `net.*` / `timeout*`
- `Screenplay(title, author)` + `.scene/.action/.character/.parenthetical/.dialogue/.transition(text)` (zincirlenir) · `.problems() -> list[str]`
- `to_fountain(sp) -> str` · `build_pdf(sp, target=None) -> bytes | Path` · `turkish_upper(text)` · `Kind`
- `analytics.fetch_rows(db, since=, until=)` · `daily_costs(rows, days=30, now=)` · `provider_breakdown(rows)` · `model_table(rows)` · `token_totals(rows)` · `budget_status(spent, budget_usd) -> Budget(level: none|ok|warn|over)` · `chart_data(rows, days=, budget_usd=, now=) -> ChartData`
**Karar:**
- Kullanıcının serbest metinleri çevrilmez; adaptörler olduğu gibi kullanır (çeviri yok, çünkü LLM bu adımda yok)
- Stil kütüphanesi: ortak `data/styles.json` (8 stil, Türkçe ad + İngilizce tarif) + model başına `data/<model>.json` (negatif varsayılanlar, model parçaları, `params` ör. Midjourney `--s`, SDXL `cfg`)
- Ortak `StockItem` ve `StockProvider` `providers/stock.py` içinde (üç sağlayıcının ortak tabanı; listeye eklenen tek ek dosya)
- Kritik alanlar: subject, lighting, style, aspect. Diğerleri (angle, lens, mood, negatives) isteğe bağlı, soruları yine de listelenir
- DALL-E negatif desteklemez → istenmeyenler `params.dropped_negatives` içinde raporlanır
- Fountain: sahne başlıkları `.` ile zorlanır; karakterler `@`; geçişler `>`; Türkçe büyük harf i→İ, ı→I. Karakter bloğu boş satırsız yazılır
- PDF: JetBrains Mono gömülü (`ui/assets/fonts`), A4, sol kenar 1,5 in; Türkçe glif varlığı test edilir
- `analytics.fetch_rows` `ProjectDB._rows` kullanır (db.py'de genel okuma yok; db.py değiştirilmedi)
- Pexels/Pixabay/Freesound ping profilleri yerine `install` ile tam arama sınıfları geçer

**Bilinen eksikler:**
- Gerçek sağlayıcı API'leri denenmedi; tüm HTTP sahte (`httpx.MockTransport`)
- Stil parçaları İngilizce öneriler; gerçek modellerde kaliteleri denenmedi
- PDF metni okunarak doğrulanmadı (pypdf yok); yalnız yazı tipi gömme ve glif varlığı kontrol edildi
- Freesound indirme yok (OAuth gerekir); yalnız önizleme ve sayfa adresi
- Fountain okuyucu yok (yalnız yazma); analitik grafikleri UI'da yok (veri hazır)

---
## Ayrıntılar (karşı thread okumaz)

**Durum:** tamamlandı, commit atılmadı.

**Dosyalar**
- `prompt_adapter/base.py`: `PromptSpec` (normalize: boşluk, virgüllü istenmeyen listesi, tekrarsız; oran listede değilse Türkçe `PromptError`), `missing_slots` (kritik önce), `resolve_style` (kimlik ya da Türkçe ad, büyük/küçük harf duyarsız), `merge_negatives` (kullanıcı önce, model varsayılanları sonra).
- Adaptörler: Midjourney `--ar … --style raw --s … --no …` (text'te bayraklar); FLUX tek cümle + "No …" (negatif alanı yok); Imagen etiketli bölümler + ayrı `negative`; DALL-E paragraf + boyut (kare 1024², yatay 1792x1024, dikey 1024x1792); SDXL `(konu:1.2)`, `(stil:1.1)`, negatifte `(low quality:1.4)`, 1344x768 varsayılan, 30 adım, cfg 7.0 (stil cfg'yi değiştirebilir), dpmpp_2m_karras.
- `providers/stock.py`: `StockProvider(BaseProvider)`; `search` zaman aşımlı GET (connect ≤3 sn); anahtar `vault.get_secret` ile; kasa hatası aynen geçer; ağ/zaman aşımı `_translate` ile; HTTP durumu `provider.*` kartlarına; hata ayrıntılarında anahtar `redact` edilir.
- `export/fountain.py`: `Screenplay` modeli + `to_fountain`. `export/pdf.py`: `build_pdf` (sayfa numarası sağ altta; başlık sayfası varsa).
- `analytics.py`: saf toplama fonksiyonları; `chart_data` Legend grafikleri için dizileri döner.

**Doğrulama**
- `tests/ai_easy` 51 passed: kısıtlamalar, eksik alan soruları (Türkçe tek cümle, kritik önce), stil kütüphanesi (8 stil, her model dosyası tam), beş adaptörün biçimi, sahte HTTP ile üç sağlayıcı (başarı, anahtar başlığı/sorgusu, sınır, eksik anahtar ağsız, kilitli kasa, çevrimdışı ve zaman aşımı, HTTP durum eşlemesi, anahtar sızmıyor), Fountain yapısı ve uyarılar, PDF (`%PDF`, gömülü yazı tipi, Türkçe glifler, dosyaya yazma), analitik (günlük dolgu, sağlayıcı ve model tabloları, bütçe düzeyleri, `ProjectDB` ile uçtan uca okuma).
- Marka taraması: yeni dosyalarda "KADRAJ" yalnızca docstring'lerde geçer.
