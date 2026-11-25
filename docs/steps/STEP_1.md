# Adım 1 — Çekirdek iskelet ve sözleşmeler (Sonnet)

## DEVİR
**Yeni dosyalar:** `main.py conftest.py pytest.ini requirements{,-ai}.txt LICENSE tools/` · `kadraj/{branding,paths,about,consent,updates}.py` · `kadraj/core/{contracts,context,events,errors,i18n,registry,settings,strings,stubs}.py` · `kadraj/storage/{db,project}.py` · `kadraj/ui/{fonts,scaling}.py` · `kadraj/ui/{theme,widgets,shell}/*` · `kadraj/ui/assets/fonts/*` · `tests/core/*`
**Test:** `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q tests/core | tail -20` → `147 passed` · `python main.py --selftest` → `SELFTEST TAMAM`
**Haiku'nun kullanacağı imzalar** (`kadraj/core/contracts.py`, değiştirilmez):
- `register() -> ModuleSpec` (`kadraj/modules/<ad>/module.py`) — modül girişi; ağır import yok, fabrikalar tembel
- `ModuleSpec(id, title_key, order, accent, icon, desc_key, chip, shortcut, workspace, simple_page, legend_page, service_factory, requires, strings)` — modül özeti; `.validate()`
- `PageFactory = (ctx) -> QWidget` — her çağrıda yeni sayfa; durum widget'ta tutulmaz
- `Service{name, start(ctx), stop()}` / `ServiceBase` — modül durum+iş mantığı; `ctx.service(id)` ile alınır
- `ContextAPI` (`ctx`): `settings.get/set/update` · `bus.emit/subscribe` · `project` · `jobs` · `vault` · `recent` · `undo` · `theme` · `ui_mode` · `report_error(exc, context)` — uygulama bağlamı
- `JobQueueAPI.register_handler(kind, handler)` / `submit(kind, payload, title) -> Job` / `cancel(id)` / `get(id)` / `list(status)` — iş kuyruğu (şimdi bellek-içi yer tutucu; Adım 3)
- `VaultAPI.providers/has_secret/masked/set_secret/get_secret/delete_secret/is_available` — kasa (şimdi bellek-içi; **yalnız `masked()` göster**)
- `SmartError(code, title, summary, *, cause, steps, actions, severity, technical, retryable, source)` · `SmartAction(id, label, kind, payload)` — Akıllı Hata verisi
- `ErrorRule(id, matches(exc, ctx), build(exc, ctx) -> SmartError, priority)` — `kadraj/core/error_catalog.py` içinde `RULES: Sequence[ErrorRule]`
- `kadraj.core.errors.translate(exc, context=None) -> SmartError` — istisnayı sade Türkçe karta çevirir, asla atmaz
- `RecentEntry(path, name, last_opened, kind)` · `RecentAPI.get_recent/add_recent(path, name=None)/forget/clear` — `storage/recent.py` bu 4 işlevi modül düzeyinde sunar
- `storage/folders.py: ensure_project_folders(project_dir: Path) -> list[Path]` (+ isteğe bağlı `ensure_root(root)`) — klasör iskeleti; sabitler `PROJECT_SUBFOLDERS`, `HIDDEN_*`
- `WizardAnswers(tasks, place, root_path, api)` · `Topics.*` (bus konuları) · `Accent`, `UiMode`, `WORKSPACES` — ortak sabitler/tipler
- contracts dışı: `kadraj.core.i18n.t(key, **fmt) / term(key) / register_strings({"tr":…,"en":…})`; `kadraj.ui.widgets` (`Lockup SquishyButton ChoiceCard ChoiceGroup Stepper DropZone SmartErrorCard HelpBubble TaskCard`); `kadraj.ui.theme.{ThemedMixin,current_tokens}`
**Kurallar:** "KADRAJ" yazısı yalnız `Lockup` (AST testi var) · ses çıktısı yalnız `.wav` · ağ çağrıları zaman aşımlı → `SmartError` · testlerde `ctx` + `qtbot` fikstürleri
**Bilinen eksikler:** vault/jobs/sysmon yer tutucu (Adım 3) · "10 Dk Önceye Döndür" yalnız olay yayınlar · KPI kutuları "—" · `PreviewTooltip`/`DockPanel` yok (Adım 7) · yalnız offscreen + Python 3.14 sınandı (gerçek ekran, 3.12 Docker'a kaldı) · `Dockerfile.test`/`KADRAJ.spec` yazılmadı · sapmalar ve kararlar §4'te

---
## Ayrıntılar (karşı thread okumaz)

**Durum:** tamamlandı, commit atılmadı. `python main.py --selftest` ✓ · `pytest tests/core` → **147 passed** ✓ ·
ana pencere iki modda açılıyor ✓ · ağ kapalıyken (soket bağlantısı engelli) hata yok ✓.

Ekran görüntüleri (offscreen, selftest çıktısı): [`step1/simple.png`](step1/simple.png) · [`step1/legend.png`](step1/legend.png)

## 1. Yapılanlar

| Alan | İçerik |
|---|---|
| **İskeletten taşınanlar** | Ayar deposu (`~/.kadraj/settings.json`, atomik yazma, bozuk dosya `.bad`'a taşınır), i18n `t()` yapısı (+ `term()` mod-duyarlı terimler), onay (`consent`), güncelleme denetimi (`updates`, yalnızca kullanıcı isteğiyle), son projeler (yedek uygulama), proje dosyası mantığı (`format`+`version`, bozuk/yabancı/yeni dosyada sade hata), ölçekleme (`ui/scaling.py`: `fit_window`), açılış akışı splash → onay → sihirbaz → ana pencere. `LICENSE`'ta SoilClassPro MIT atfı aynen korunur, üstüne KADRAJ telif satırı eklendi. |
| **Çekirdek** | `core/contracts.py` (aşağıda), `context.py` (`AppContext`), `settings.py`, `i18n.py` + `strings.py` (TR/EN), `events.py` (`EventBus`), `registry.py` (otomatik keşif + planlı modüller), `errors.py` (`SmartError` + `translate`), `stubs.py` (kasa/iş kuyruğu/sistem/son projeler yer tutucuları). |
| **Depolama** | `storage/db.py` (plan §3 şeması birebir, `user_version` göçü, thread başına bağlantı, WAL), `storage/project.py` (`ProjectData`, `ProjectSession`, klasör iskeleti, atomik yazma, ileri uyumluluk için bilinmeyen alanları korur). |
| **Tema** | `ui/theme/tokens.py` (§4 renkleri birebir; Büyük Yazı ×1.25 ve Yüksek Kontrast katmanları), `qss.py`, `manager.py` (480 ms çapraz geçiş; Hareketi Azalt → 120 ms). |
| **Widget'lar** | `Lockup`, `ModeSwitch`, `SquishyButton`, `HelpBubble` (+`Mascot`, `MascotTip`), `StatusBar` (+`AutosaveIndicator`), `SmartErrorCard`, `TaskCard`, `ChoiceCard`/`ChoiceGroup` (= `QualityPicker`/`PlatformCard` kısayolları), `DropZone`, `Stepper`, çizgi ikon seti (`icons.py`). |
| **Kabuk** | `splash.py`, `wizard.py` (3 soru, tuvaldeki sorularla), `dialogs.py` (onay, hakkında, hata penceresi), `home.py` (Basit ana ekran: görev kartları, son projeler, bırakma alanı, maskot), `legend.py` (Proje merkezi + KPI şeridi, çalışma alanları, Hızlı Başlat / Görev Kuyruğu / Sistem dock'ları), `topbar.py`, `main_window.py`, `app.py` (`create_app`, `Launcher`, `run`). |
| **Üst çubuk** | Lockup · proje menüsü · **Geri Al** (`ctx.undo`) · **10 Dk Önceye Döndür** (şimdilik `rewindRequested(10)` + `Topics.REWIND_REQUESTED`; durum çubuğunda dürüst mesaj) · otomatik kayıt göstergesi (`Topics.AUTOSAVE_STATE`'i dinler) · canlı Basit/Legend anahtarı · ayarlar dişlisi. Dar pencerede / Büyük Yazı'da Geri Al ve Döndür ikon-only olur. |
| **Kök** | `main.py` (`--selftest [--screenshots DIR]`, `--launcher`, `--mode`), `requirements.txt`, `requirements-ai.txt`, `.gitignore`, `pytest.ini`, `conftest.py` (offscreen + yalıtılmış ev dizini + `ctx` fikstürü), `README.md`, `LICENSE`, `tools/make_static_fonts.py`. |
| **Yazı tipleri** | `ui/assets/fonts/` altında gömülü: Nunito, Archivo (+ Archivo Expanded = wordmark `wdth=125`), Inter, JetBrains Mono, Instrument Serif; her aileye `OFL-*.txt`. |

## 2. Sözleşme özeti (`kadraj/core/contracts.py` — Adım 1'den sonra dokunulmaz)

* **`ModuleSpec`** (frozen dataclass): `id, title_key, order, accent: Accent, icon, desc_key, chip, shortcut, workspace, simple_page, legend_page, service_factory, requires, strings`; `validate()` sözleşme ihlallerini listeler.
* **`PageFactory`** `(ctx) -> QWidget`: her çağrıda **yeni** widget; durum widget'ta tutulmaz, `ctx.service(id)`'e bağlanır. Mod değişince sayfa atılıp yeniden kurulur.
* **`Service`** (`name`, `start(ctx)`, `stop()`) + `ServiceBase`; `ctx.service(id)` ilk istekte üretip başlatır.
* **`Job` / `JobStatus` / `JobContext` / `JobQueueAPI`** (`register_handler`, `submit`, `cancel`, `get`, `list`) — Adım 3 gövdesi; şimdi `InMemoryJobQueue` yer tutucusu.
* **`Provider` / `ProviderKind` / `PingResult`** (`is_configured(vault)`, `ping(vault, timeout)` — asla istisna atmaz).
* **`VaultAPI`** (`is_available, providers, has_secret, masked, set_secret, get_secret, delete_secret`, `is_placeholder`) — Adım 3 gövdesi; şimdi bellek-içi `InMemoryVault` (**diske yazmaz**).
* **`SmartError`** (`code, title, summary, cause, steps, actions: SmartAction, severity, technical, retryable, source`) · **`ErrorRule`** (`id, matches(exc, ctx), build(exc, ctx), priority`) · **`Translator`** = `kadraj.core.errors.translate(exc, context=None)`; sözlük `kadraj/core/error_catalog.py::RULES: Sequence[ErrorRule]` (yoksa genel kartlar).
* **Depolama kancaları:** `RecentEntry`, `RecentAPI` (`get_recent/add_recent/forget/clear`, `recent.json` biçimi belgeli), `WizardAnswers`, `PROJECT_SUBFOLDERS`, `PROJECT_FILE_NAME`, `HIDDEN_DIR`…
* **`ContextAPI`**: `settings, bus, project, jobs, vault, recent, undo, theme, ui_mode, set_ui_mode(), service(), report_error()`.
* **`Topics`**: `MODE_CHANGED, LANGUAGE_CHANGED, A11Y_CHANGED, NAVIGATE, NOTIFY, PROJECT_*, AUTOSAVE_STATE, REWIND_REQUESTED, JOB_*, ERROR, SYSMON`.
* **Registry keşif kuralları** (`DISCOVERY_RULES`, 8 madde): `kadraj/modules/<ad>/module.py` içinde argümansız `register() -> ModuleSpec`; ağır içe aktarmalar fabrikalarda tembel; `spec.strings` (`"<id>."` önekli) i18n'e birleşir; hata veren modül atlanır → `registry.errors`; planlı ama eksik modüller ana ekranda "yakında" kartı olur.

## 3. Haiku (Adım 2, 4, 5) için bağlanma noktaları

* `kadraj/storage/folders.py` → `ensure_project_folders(project_dir: Path) -> list[Path]` (varsa `ProjectSession` bunu kullanır) ve isteğe bağlı `ensure_root(root: Path)` (sihirbaz bitince çağrılır).
* `kadraj/storage/recent.py` → modül düzeyinde `get_recent() -> list[RecentEntry]`, `add_recent(path, name=None)`, `forget(path)`, `clear()` (dördü de varsa `ctx.recent` bunu kullanır; yoksa yedek).
* `kadraj/core/error_catalog.py` → `RULES: Sequence[ErrorRule]`.
* Modül: `kadraj/modules/<ad>/module.py::register()`, `strings.py` (`STRINGS={"tr":…,"en":…}` — anahtarlar `"<ad>."` ile başlar; çekirdek anahtarın üstüne yazılamaz), sayfa fabrikaları. Widget'lar için `kadraj.ui.widgets`, renkler için `ctx.theme.tokens` / `current_tokens()`, özel çizimde `ThemedMixin`.
* Test: `conftest.py`'deki `ctx` fikstürü (temiz ayar, animasyonsuz tema) + `qtbot`; gerçek `~/.kadraj`'a hiç dokunulmaz.
* Kural: "KADRAJ" yazısı yalnızca `Lockup` ile (kaynak taraması `tests/core/test_brand_rule.py`'de); ses çıktısı yalnızca `.wav`.

## 4. Sapmalar ve kararlar (koordinatör gözden geçirsin)

1. **Legend çalışma alanları** plan §2'deki 7 yerine tuvalin **v2** sayfasındaki 5'i: Proje, Kurgu, Stüdyo, Yakala, Sistem (`contracts.WORKSPACES`; tek yerden değişir).
2. **Yazı tipleri sabit örnek olarak gömülü.** Qt, değişken TTF'nin yalnızca varsayılan örneğini kaydediyor (Nunito "ExtraLight"e kilitleniyordu; QSS `font-weight` etkisizdi). `tools/make_static_fonts.py` değişken dosyalardan (github.com/google/fonts, OFL) 16 sabit örnek üretti; değişken kaynaklar repoda yok. Wordmark gerçek `Archivo Expanded ExtraBold` (`wdth=125`) ile çiziliyor.
3. **`LICENSE` telif sahibi** "İnsibağ (KADRAJ)" olarak yazıldı — adı sizin doğrulamanız gerekir.
4. **Sihirbaz API anahtarını saklamaz**: "yapıştırayım" yalnızca bilgi notu gösterir; kasa arayüzü Adım 3/5. "Nasıl alırım?" seçilirse ana pencere açılınca Ayarlar'a (şimdilik "yakında") yönlendirir.
5. **Sihirbazı kapatan** (Esc) kullanıcı varsayılanlarla devam eder ve sihirbaz bir daha çıkmaz (`wizard.done`).
6. **Modun kendisi projeyi kirletmez:** `ui_mode_last` değişince "kaydedilmemiş değişiklik" sorulmaz; kapanışta sessizce yazılır.
7. **`ctx.set_ui_mode`** temayı da uygular (pencere yokken de çalışır); `ThemeManager.apply` değişiklik yoksa QSS'i yeniden kurmaz.
8. Ayrı `Dockerfile.test` ve `KADRAJ.spec` yazılmadı (bu adımın kapsamında değildi).

## 5. Test çıktısı

```
$ QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/core
........................................................................ [ 48%]
........................................................................ [ 97%]
...                                                                      [100%]
147 passed in 18.77s            # -W default ile de uyarısız
```

Kapsam: `test_contracts` (Protocol uyumu dahil) · `test_settings` · `test_events` · `test_i18n` (TR/EN anahtar eşitliği, koddaki her anahtarın varlığı, mod terimleri) · `test_errors` · `test_registry` (bozuk modül yalıtımı) · `test_db` · `test_project` · `test_theme` (§4 tokenları birebir, 480/120 ms, çapraz geçiş) · `test_widgets` (imza oranı, imzanın "J" ile piksel hizası, mod anahtarı, düğme, hata kartı…) · `test_shell` (iki mod, durum/geri alma/gezinti korunumu, kısayollar, sihirbaz, splash, onay, akış, çevrimdışı) · `test_brand_rule` (AST taraması) · `test_selftest` (ayrı süreçte `main.py --selftest`).

```
$ python main.py --selftest
[ok] sürüm — 0.1.0
[ok] gömülü yazı tipleri — Nunito=ok, Archivo=ok, Archivo Expanded=ok, Inter=ok, JetBrains Mono=ok, Instrument Serif=ok
[ok] kayıt defteri keşfi — 0 modül kayıtlı, 6 kart
[ok] imza oranı (0.30, ≥9 px)
[ok] i18n tr/en
[ok] proje oluşturma (klasörler + dosya + db)
[ok] db şeması
[ok] ana pencere simple modunda açıldı — proje korundu: Selftest Projesi
[ok] ana pencere legend modunda açıldı — proje korundu: Selftest Projesi
[ok] ana pencere simple modunda açıldı — proje korundu: Selftest Projesi
[ok] mod geçişinde proje durumu korundu
[ok] ağa çıkma girişimi yok — 0 deneme
SELFTEST TAMAM
```

Ek elle denetim: gerçek açılış (`main.py` ve `main.py --launcher --mode legend`, offscreen) 6,5 sn boyunca hatasız çalıştı; splash, sihirbaz (3 sayfa × 2 mod), onay/hakkında diyalogları, widget galerisi, Büyük Yazı ve Yüksek Kontrast her iki modda ekran görüntüsüyle gözle kontrol edildi.

## 6. Bilinen eksikler / sonraki adımlar

* **Yer tutucular:** `ctx.vault` (bellek-içi), `ctx.jobs` (bellek-içi, kalıcı değil), `ctx.sysmon` (yalnızca psutil; GPU yok) → Adım 3. Otomatik kayıt göstergesi "Henüz kayıt yok" der; 10 Dk Önceye Döndür yalnızca olay yayınlar. KPI kutuları (render, token) Adım 3/8'e kadar "—".
* **Henüz yok:** `PreviewTooltip`, `DockPanel` widget'ları (Adım 7); `QualityPicker`/`PlatformCard` şimdilik `ChoiceGroup`/`ChoiceCard` takma adları; tüm modül sayfaları "yakında" yer tutucusu.
* **Doğrulama sınırı:** yalnızca `offscreen` platformunda (gerçek xcb/Wayland penceresi, kesirli DPI, çoklu monitör dock koparma elle denenmedi); Python 3.12 yorumlayıcısı bu makinede yok → Docker adımına kalır (kod 3.12 sözdizimiyle uyumlu yazıldı, 3.14 `.venv`'de sınandı); Windows/macOS denenmedi.
* `updates.GITHUB_REPO` boş (depo yayınlanınca doldurulur); denetim yalnızca Yardım menüsünden, kullanıcı isteğiyle.
* Çalışma zamanında ağ kullanan tek yer o düğme; geliştirme sırasında ağ yalnızca `pip` (14 çekirdek paket → `.venv`), yazı tipi dosyaları ve tuval okuması için kullanıldı.

## 7. Dosya listesi

```
KADRAJ/
  main.py  conftest.py  pytest.ini  requirements.txt  requirements-ai.txt  .gitignore  LICENSE  README.md
  tools/make_static_fonts.py
  docs/steps/STEP_1.md  docs/steps/step1/{simple,legend}.png
  kadraj/__init__.py  version.py  branding.py  paths.py  about.py  consent.py  updates.py
  kadraj/core/        __init__ contracts context events errors i18n registry settings strings stubs
  kadraj/storage/     __init__ db project
  kadraj/modules/     __init__            (boş paket; modüller Adım 4+)
  kadraj/ai_engine/   __init__  providers/__init__
  kadraj/ui/          __init__ fonts scaling
  kadraj/ui/theme/    __init__ tokens qss manager
  kadraj/ui/widgets/  __init__ icons lockup mode_switch squishy_button help_bubble status_bar
                      smart_error_card task_card choice drop_zone stepper
  kadraj/ui/shell/    __init__ app startup splash dialogs wizard pages home legend topbar main_window
  kadraj/ui/assets/fonts/  (23 dosya: 18 TTF + 5 OFL-*.txt)
  tests/__init__.py  tests/core/  __init__ test_{contracts,settings,events,i18n,errors,registry,db,project,
                                  theme,widgets,shell,brand_rule,selftest}.py
  .venv/  (geliştirme sanal ortamı, .gitignore'da)
```
