# Adım 2 — Proje ve depolama (Haiku)

## DEVİR
**Yeni dosyalar:** `kadraj/storage/{folders,recent,disk,cache,profiles,onboarding}.py` · `kadraj/core/error_catalog.py` · `tests/storage/*` · `docs/steps/STEP_2.md`
**Test:** `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -o addopts="" -q tests/storage` → `97 passed` · `… tests/core` → `147 passed`
**Herkese açık imzalar** (sözleşme dosyası değişmedi):
- `folders.ensure_project_folders(project_dir) -> list[Path]` · `ensure_root(root)` · `folder_for(kind)` · `FOLDER_BY_KIND`
- `recent.get_recent() / add_recent(path, name=None) / forget(path) / clear()` — `stubs.make_recent()` bunu otomatik kullanır
- `disk.status(path)` · `has_room_for(path, needed_bytes, reserve=2 GB)` · `warning_for(path) -> SmartError | None` · `insufficient_error(path, needed)` · `human_size(n)`
- `cache.usage(project)` · `cache.clean(project, categories, *, older_than_days, now, dry_run) -> CleanReport`
- `profiles.list_profiles / active_profile / get_profile / create_profile / update_profile / switch_profile / delete_profile` · `Profile.vault_namespace` · `ProfileError.code`
- `onboarding.run(settings, answers, *, first_project=None) -> OnboardingResult(root, project_dir, folders)`
- `error_catalog.RULES` — 22 kural (FFmpeg, yt-dlp, HTTP, CUDA, ses girişi)
**Yeni hata kodları:** `ffmpeg.{disk_full,corrupt_input,no_encoder,permission,failed}` · `ytdlp.{geo_blocked,age_restricted,private,unavailable,unsupported_url,connection,http_403,generic}` · `http.{unauthorized,forbidden,rate_limited,server_error,timeout}` · `cuda.{oom,unavailable}` · `audio.no_device` · `disk.{low,critical,insufficient}`
**`open_settings` bölüm kimlikleri** (Adım 5 bunları eşlemeli): `tools` · `updates` · `ai` · `fallback` · `audio` · `system` · `storage`
**Bilinen eksikler / kararlar:**
- Profil `ui_mode` ile `settings.ui_mode` iki ayrı kaynak; kabuğun hangisini okuyacağı henüz bağlanmadı (koordinatör karar verir)
- Profil silmek kasa sırlarını silmez; kasa `profile.<id>` ad alanına göre temizlemeli (Adım 3)
- Bozuk profil katmanı listede gizlenir, dosyası silinmez
- `ffmpeg.missing` yok: eksik araç `tool.missing` (errors.py) ile kalır, mevcut `test_errors` sözleşmesi bunu bekliyor
- Disk eşikleri sabit (10 GB uyarı, 2 GB kritik); ayarlanabilir değil
- Disk kartları `storage/disk.py` içinde, `error_catalog` içinde değil
- Commit atılmadı; HANDOFF satırı koordinatörde

---
## Ayrıntılar (karşı thread okumaz)

**Durum:** tamamlandı, commit atılmadı. Yeni bağımlılık yok (yalnızca stdlib; PySide6 zaten var).

**Dosyalar ve davranış**
- `storage/folders.py`: `ensure_project_folders` 7 görünür klasörü ve `.kadraj/{autosave,snapshots,cache}` alt klasörlerini `mkdir(exist_ok)` ile kurar; hiçbir şey silmez. Yol dosya olarak duruyorsa `OSError` yükselir.
- `storage/recent.py`: `~/.kadraj/recent.json` (`KADRAJ_HOME` ile değişir), atomik yazma. `get_recent` diskte olmayan klasörleri gizler ama dosyaya geri yazmaz (harici disk takılı değilse kayıt kaybolmaz). `add_recent` var olmayan klasörü eklemez. Yazma hatası sessiz geçer (son projeler kolaylık, uygulamayı durdurmaz).
- `storage/disk.py`: `shutil.disk_usage` üzerinden; yol yoksa en yakın var olan üst klasör ölçülür. Ölçüm hatasında `Level.UNKNOWN` döner ve hiçbir şey engellenmez. Kartlar `disk.low` (uyarı) / `disk.critical` (hata) / `disk.insufficient` kodlarını taşır. Sayılar Türkçe ondalık virgüllüdür (`12,3 GB`).
- `storage/cache.py`: kategoriler `cache` (`.kadraj/cache`), `proxies` (`06_Proxies`), `partial` (`*.part`, `*.part-FragN`, `*.ytdl`, `*.temp`; proje alt klasörlerinde, `06_Proxies` hariç). `autosave`, `snapshots`, `proje.db`, `proje.kadraj` taranmaz. Simge bağlantılar izlenmez; proje dışına çözülen dosyalar atlanır. `dry_run` ve `older_than_days` desteklenir; silme hataları `failed` sayısına yazılır.
- `storage/profiles.py`: `profiles/index.json` (etkin profil + sıra) ve `profiles/<id>.json` (katman: `ui_mode`, `a11y`). Kimlik Türkçe harfleri sadeleştiren `slugify` ile üretilir; yol dışına çıkan kimlikler reddedilir. Varsayılan profil silinemez. Yazma hataları `OSError` olarak yükselir; okuma bozuk dosyada varsayılana döner.
- `storage/onboarding.py`: sıra bilinçli: önce `ensure_root` (ve varsa ilk proje iskeleti), sonra `apply_wizard_answers`. Klasör hatasında ayarlar değişmez. `first_project` boş ya da `None` ise proje oluşturulmaz. `clean_project_name` `: / \ * ? " < > |` karakterlerini boşluğa çevirir, `..` gibi adları reddeder.
- `core/error_catalog.py`: `RULES` 22 kuraldır. Özel kurallar 10–400 önceliğinde; araca bağlı genel yedekler (`ffmpeg.failed`, `ytdlp.generic`) 90 ve 190'dadır ve OSError ile ağ/zaman aşımı hatalarını dışlar (yerleşik kartlara bırakır). Metinler `err.cat.<kural>.{title,summary,cause,step1}` anahtarlarıyla TR/EN olarak modül açılışında `register_strings` ile eklenir; çekirdek tablo değiştirilmedi.

**Test düzeni:** tüm testler `clean_home` (`KADRAJ_HOME` geçici dizin) kullanır; ağ çağrısı yok; disk ölçümü `shutil.disk_usage` sahteleriyle yapılır.
- `tests/storage/test_folders.py` · `test_recent.py` · `test_disk.py` · `test_cache.py` · `test_profiles.py` · `test_onboarding.py` · `test_error_catalog.py`
- Çıktı: `97 passed` (storage) · `147 passed` (core, marka ve i18n testleri dahil).

**Sınır ve notlar**
- Başlangıçta `tests/core` üç hata verdi: iki tanesi benim (`ffmpeg.missing` kuralı `tool.missing` sözleşmesini gölgeliyordu, kaldırıldı; `onboarding` içinde literal `t("…")` anahtarı i18n testine takılıyordu, sabite çevrildi). Üçüncüsü (`ContextAPI` kontrolü, `jobs.notify.*` ve `provider.*` anahtarları) Sonnet'in o sırada değişen şerit-3 dosyalarındaydı; sonraki çalıştırmada geçti.
- `i18n` testi yalnızca literal `t("…")` çağrılarını çekirdek tabloda arar. Bu şeritteki modül dizeleri dinamik anahtarla (`f"…"` ya da sabit) kullanılır ve `register_strings` ile eklenir.
- Doğrulanmadı: Docker/ayrı venv (koordinatörün son kontrolü), ekran görüntüsü (bu şeritte arayüz yok).
- Ortam: mevcut `KADRAJ/.venv` (Python 3.14) kullanıldı; yeni venv ya da imaj oluşturulmadı, silinecek bir şey yok.
