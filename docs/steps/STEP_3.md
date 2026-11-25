# Adım 3 — Güvenlik ve sistem (Sonnet)

## DEVİR
**Yeni:** `kadraj/storage/{vault,autosave,snapshots}.py` · `kadraj/core/{jobs,sysmon}.py` · `kadraj/ai_engine/providers/base.py` · `tests/security/*` (8 dosya)
**Değişen:** `core/{contracts(§9 yalnız ekleme),context,errors,settings,strings}.py` · `storage/{project,db}.py` · `ui/shell/{main_window,legend}.py` · `conftest.py` (sahte keyring) · `main.py` (selftest)
**Test:** `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q tests/security tests/core | tail -5` → `312 passed` (165 security + 147 core; tüm ağaç 408) · `python main.py --selftest` → `SELFTEST TAMAM`
**Haiku Adım 5'in kullanacağı imzalar** (`ctx.vault`, `ctx.providers`; tipler `core/contracts.py` §4 ve §9):
- `VaultAPI.set_secret(provider_id, value)` / `get_secret(id) -> str|None` / `delete_secret(id) -> bool` — yazma/düz okuma; kasa açılamazsa `SmartError` (düz anahtarı ekrana/loga yazma)
- `VaultAPI.has_secret(id)` / `masked(id) -> "sk-a••••3f9a"` / `providers() -> list[str]` — **atmaz**; kilitli/bozuksa `False`/`""`/`[]` (önce `status()` bak)
- `VaultAdminAPI.status() -> VaultStatus(state: empty|ready|locked|error, mode: keyring|passphrase|none, namespace, error)` — kasa durumu
- `VaultAdminAPI.unlock(passphrase)` / `lock()` / `needs_unlock()` — parola kipinde oturum açma (yanlışsa `SmartError vault.wrong_passphrase`)
- `VaultAdminAPI.initialize(passphrase=None)` / `change_passphrase(old, new)` / `reset()` — keyring yoksa parola belirle (≥8); `reset` eski dosyayı `.bad` yapar
- `VaultAdminAPI.use_namespace(ns)` / `view(ns) -> VaultAPI` / `namespaces()` / `delete_namespace(ns)` — profil ad alanı (Haiku `profiles.py` adı üretir)
- `ctx.providers.test_connection(provider_id, vault=ctx.vault, timeout=5.0) -> PingResult(ok, latency_ms, message, error: SmartError|None)` — test ışıkları; asla atmaz
- `ctx.providers.list(kind=None)` / `get(id)` / `configured(vault, kind)` — kayıtlı: anthropic openai_compat bfl_flux google_imagen elevenlabs pexels pixabay freesound ollama comfyui whisper_local
- `ctx.providers.chain(kind, vault)` / `call_with_fallback(kind, operation, vault=, queue_payload=, queue_title=) -> FallbackResult` — bulut→yerel→kuyruğa al
**Haiku Adım 4'ün (indirici) kullanacağı imzalar** (`ctx.jobs`, `ctx.snapshots` gerekmez):
- `ctx.jobs.register_handler(kind, handler(job, jc))` / `submit(kind, payload, title="", *, notify=True, max_retries=0) -> Job` — iş kuyruğu; işleyici iş parçacığında koşar, `jc.report(0..1, mesaj)`, `jc.is_cancelled()`, dönen `dict` → `job.payload["_result"]`
- `ctx.jobs.cancel(id)` / `retry(id)` / `resume(id)` / `get(id)` / `list(status)` — denetim; olaylar `Topics.JOB_ADDED/JOB_UPDATED`; `_` önekli payload anahtarları kuyruğundur
**Diğer yeni yüzeyler:** `ctx.autosave.rewind(minutes)/undo_rewind()/ring()` · `ctx.snapshots.create/restore/branch_from/compare/list/delete` · `ctx.sysmon.sample(path)` · `Topics.PROJECT_RESTORED` · `ProjectSession.{aboutToClose,dataReplaced,replace_data,save(silent=)}`
**contracts.py:** yalnız ekleme — `VaultStatus VaultAdminAPI Attempt FallbackResult ProviderRegistryAPI JobControlAPI RingEntry RewindResult AutosaveAPI SnapshotInfo SnapshotDiff SnapshotAPI SysmonAPI`, `Topics.PROJECT_RESTORED`, `ContextAPI`'ye `sysmon providers autosave snapshots`; mevcut imzalar değişmedi
**Bilinen eksikler:** gerçek OS keyring testlerde yok (yalnız bellek-içi sahte; Linux SecretService/KWallet elle denenmedi) · parola açma, snapshot ağacı ve test-ışığı arayüzleri yok (Adım 5/7) · BFL/Imagen ping uç noktaları tahmin (Adım 8 doğrular) · autosave senkron yazar, büyük zaman çizelgelerinde iş parçacığına alınmalı (Adım 7) · duraklatma işleyicinin `is_cancelled()`'ı izlemesine bağlı (kapanışta en çok 3 sn beklenir) · masaüstü bildirimi gerçek tepside denenmedi (offscreen: sessiz) · proje DB'si (media/jobs) geri dönüşte geri sarılmaz, yalnız proje dosyası

---
## Ayrıntılar (karşı thread okumaz)

**Durum:** tamamlandı, commit atılmadı. `pytest -q tests/security` → 165 passed; `tests/core` → 147 passed; tüm ağaç (Haiku'nun `tests/storage`'ı dahil) → 408 passed; `-W default` uyarısız; selftest ✓; gerçek açılış (`main.py --launcher --mode legend`, offscreen, `autosave_seconds=5`) 8 sn hatasız, açılışta `vault.bin` oluşmuyor.

### 1. Ne yapıldı
| Parça | Davranış |
|---|---|
| **Kasa** `storage/vault.py` | Fernet. Dosya: JSON başlığı (`format, version, mode, key_id[, kdf]`) + `token`. **keyring kipi:** rastgele 32 bayt anahtar OS keyring'de (`kadraj`/`vault-key-<key_id>`), kasa kendiliğinden açılır. **parola kipi:** keyring yok/başarısızsa (yazma-okuma sınamasından geçmezse) PBKDF2-HMAC-SHA256, 600 000 yineleme, 16 bayt tuz; her oturumda `unlock`. Kasa açılamazsa (kilitli, anahtar kayıp, bozuk) **yeni kasa kurulmaz / üzerine yazılmaz**; `SmartError` (`vault.locked/wrong_passphrase/key_missing/corrupt/needs_passphrase/weak_passphrase/backend_missing/invalid_*`). Dosya `0600`, atomik yazma, düz anahtar diskte/`repr`'de/hata ayrıntısında yok. Ad alanları: `{"namespaces":{ad:{sağlayıcı:anahtar}}}`. Başlangıçta keyring'e/dosyaya dokunulmaz (tembel). `MemoryKeyring` + `set_default_keyring()` test ve selftest içindir. |
| **Sağlayıcılar** `ai_engine/providers/base.py` | `BaseProvider.test_connection()` (= `ping`): `httpx`, zaman aşımı (`connect ≤ 3 sn`), yönlendirme izlenmez, gecikme ms. 401/403→`provider.auth`, 404, 429 (`Retry-After`), 5xx, diğer; çevrimdışı/zaman aşımı → sade `SmartError` (yerel servis için "çalışmıyor"); anahtar ve URL sorgusu hata ayrıntısından silinir (`redact`). `ProviderRegistry`: sıra `settings["fallback_order"][tür]` yoksa kayıt sırasıyla bulut→yerel; `call_with_fallback`: çevrimdışıysa kalan bulutları atlar, 429'da `Retry-After`/üstel bekleme (en çok 2 yeniden deneme) sonra sağlayıcıyı soğumaya alır, hepsi başarısızsa `ai.deferred` işi olarak kuyruğa alır. `default_registry()`: 11 servisin ping profili (Adım 8 `register(p, replace=True)` ile yerine koyar). `errors.is_network_error/is_timeout_error` eklendi (Haiku'nun hata sözlüğü kod adlarını değiştirse de mantık bozulmaz). |
| **Otomatik kayıt** `storage/autosave.py` | 20 sn'de bir `tick()`: özet (`ProjectData.digest`) değişmediyse ve proje kirli değilse **diske hiç dokunmaz**; değişiklik varsa proje dosyasını sessizce (`save(silent=True)`) yazar + `.kadraj/autosave/` halkasına kayıt düşer (≈60 sn'lik pencere başına bir dosya, en çok 11; **sayıya göre** budanır, boşta kalmak geçmişi silmez; açılışta başlangıç durumu da halkaya girer). `Topics.AUTOSAVE_STATE` `{state, at, ring}`. Hata (disk dolu…) uygulamayı düşürmez; `error` durumu + **bir kez** `SmartError`. Proje kapanırken son değişiklikler kaydedilir. |
| **10 Dk Önceye Döndür** | `Topics.REWIND_REQUESTED` → `rewind(minutes)`: hedef zamandan önceki en yeni kayıt noktasına döner (geçmiş kısaysa en eskiye, kullanıcıya söyler). **Geri alınabilir:** önce güncel durum yedeklenir (en çok 3), `undo_rewind()` / **Düzen › Geri dönüşü geri al**. Sonrasında `ctx.undo` temizlenir, `Topics.PROJECT_RESTORED` yayınlanır, sonuç `Topics.NOTIFY` ile durum çubuğunda ("…döndürüldü", "…aynı", "yalnızca N dk"). |
| **Snapshot** `storage/snapshots.py` | `snapshots` tablosu + `.kadraj/snapshots/<id>.json`; ebeveynli ağaç, dal (`branch_from`), etiket, `restore` (içerik başın içeriğinden farklıysa önce "Geri yüklemeden önce" otomatik snapshot → geri alınabilir), `compare`/`compare_with_current` (kanal/klip/işaret/ayar/ad farkı, sade Türkçe), `delete` (çocukları yukarı bağlar), `prune_auto`, `tree`. Konum proje dosyasında `extra["snapshots"]`. |
| **İş kuyruğu** `core/jobs.py` | `QThreadPool` (`jobs.max_parallel`=2), işler proje DB'sinde (proje yokken bellekte, negatif kimlik). İstisna → `translate` → `FAILED` + `payload["_error"]`; `retryable` hatalar `max_retries`'e kadar kendiliğinden (üstel). Proje kapanırken/`shutdown`'da çalışanlar **duraklatılır**; proje açılınca `queued/running` kalanlar (çöküş dahil) **`paused`** döner; `resume`/`retry`. İşleyicisi olmayan iş (`ai.deferred`) bekler. Masaüstü bildirimi: `QSystemTrayIcon`, ≥5 sn süren işler, tepsi yoksa sessiz (`notifications.*` ayarları). İlerleme yayını hız sınırlı. |
| **Sistem izleyici** `core/sysmon.py` | psutil (CPU/RAM/disk) + `pynvml` (GPU/VRAM; yoksa/hata verirse bir kez denenip sessizce `None`) + `api_ping` (`ProviderRegistry.on_ping`). Sağ paneldeki Sistem'e GPU, VRAM ve API ping bağlandı. |

### 2. Sapmalar ve kararlar (koordinatör gözden geçirsin)
1. **Otomatik kayıt proje dosyasının kendisini yazar** (sessiz); halka ve snapshot'lar güvenlik ağıdır. İstenirse yalnızca halkaya yazan kipe çevrilebilir.
2. **`Provider.ping` sözleşmesi korundu**; istenen `test_connection()` `BaseProvider`'da aynı işin adıdır (sözleşmeye `ping` ile uyumlu kalmak için protokole eklenmedi).
3. **Kasa okuma yöntemleri atmaz** (`has_secret/masked/providers` kilitliyken boş döner) ki test ışıkları ve Ayarlar çökmesin; yazma/`get_secret` atar.
4. **Haiku'nun `error_catalog.py`'si artık var** (ör. zaman aşımı `http.timeout`); sağlayıcı mantığı kod adına değil istisna türüne dayandığı için etkilenmedi, testler ağ hatası ailesini (`net.*/http.*/ytdlp.*`) kabul eder.
5. UI (benim şeridim): `main_window.py` (Düzen › Geri dönüşü geri al, geri dönüş mesajları, `PROJECT_RESTORED`), `legend.py` (Sistem: GPU/VRAM/ping). `status.rewind_requested` metni "dönülüyor…" oldu.
6. `ProjectDB`'ye `get_snapshot/update_snapshot` ve `update_job(payload=, clear_error=)`; `ProjectSession`'a `aboutToClose`, `dataReplaced`, `replace_data`, `save(silent=)`; ayarlara `jobs.max_parallel`, `notifications.{desktop,min_seconds}`.
7. Kök `conftest.py`: her teste temiz bellek-içi keyring (otomatik); testler gerçek OS keyring'e dokunmaz.

### 3. Test kapsamı (`tests/security`, ağ ve keyring sahte)
`test_vault` (44: şifreleme, dosya izni/düz metin yok, keyring kayıp/bozuk/kurcalanmış, parola kipi yaşam döngüsü, ad alanları, geçersiz girdiler, eşzamanlı yazıcılar, disk dolu) · `test_providers` (44: kimlik doğrulama şemaları, durum kodları, zaman aşımı, sızıntı yok, gerçek httpx + engelli soket, fallback sıraları, 429, soğuma, kuyruğa alma) · `test_autosave` (21: yalnız-değişiklikte yazma, halka penceresi/budama, geri dönüş/kısmi/geri alma, bus, hata bir kez, kapanışta kayıt) · `test_snapshots` (16) · `test_jobs` (22: ilerleme, iptal, retry, otomatik retry, eşzamanlılık, kalıcılık, çöküş kurtarma, bildirim) · `test_sysmon` (7) · `test_integration` (11: kablolama, kabuk, kapanış, ağsız akış).

### 4. Dosya listesi
```
kadraj/storage/{vault,autosave,snapshots}.py   kadraj/core/{jobs,sysmon}.py   kadraj/ai_engine/providers/base.py
tests/security/{conftest,test_vault,test_providers,test_autosave,test_snapshots,test_jobs,test_sysmon,test_integration}.py
değişen: core/{contracts,context,errors,settings,strings}.py  storage/{project,db}.py  ui/shell/{main_window,legend}.py  conftest.py  main.py
```
