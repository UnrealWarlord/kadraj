# Adım 8c — Legend proje merkezi: KPI ve analitik gerçek veriye bağlı (Haiku)

## DEVİR
**Yeni:** `tests/core/test_legend_analytics.py` (11 test; sahte `api_usage` ve `jobs` kayıtları, ağ yok) · `docs/steps/STEP_8c.md`
**Değişen:** `kadraj/ui/shell/legend.py` (`ProjectHub`: KPI'lar + `AnalyticsPanel`; `_budget`, `_disk_text`, `_tokens_text`) · `kadraj/ai_engine/analytics.py` (`RENDER_KIND`, `month_start`, `render_count`, `MonthKpi`, `month_kpi`)
**Test:** `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -o addopts="" -q tests/core/test_legend_analytics.py` → `11 passed` · tüm ağaç (dizin dizin): core `160` · storage `97` · security `165` · recorder `253` · editor `65` · downloader `53` · settings `48` · ai `77` · ai_easy `51` → `969 passed` · `python main.py --selftest` → `SELFTEST TAMAM`
**Karar:** KPI'lar takvim ayı (yerel saat, ayın 1'i gece yarısından itibaren); grafik ve sağlayıcı tablosu kayan son 30 gün. Grafiğin bütçe çizgisi zaten aylık bütçe / 30.
**Karar:** Render sayısı proje veritabanındaki `jobs` tablosundan: `status='done'`, `kind=editor.render`, `updated` bu ayın başından büyük. `RENDER_KIND` testte `JOB_RENDER` ile eşitlenir.
**Karar:** Disk KPI'sı `storage.disk.status(kök)`; proje yoksa ayarlardaki proje kökü; kök yoksa "—". Maliyet yüzdesi yalnız `plan.budget_usd` tanımlıysa gösterilir.
**Karar:** `AnalyticsPanel` (`modules/series/charts.py`, Sonnet'in dosyası, değiştirilmedi) kabuğa içe aktarıldı: kabuk artık bir modül widget'ını kullanıyor. Proje yoksa boş veri; sakin "Henüz harcama yok" metni grafikte.
**Bilinen eksikler:** token ve maliyet KPI'ları yalnız proje açıkken (veritabanı yoksa "—"); render sayısı yalnız kalıcı `jobs` kayıtlarını sayar (bellekte bekleyen işler sayılmaz).
**Tema düzeltmesi (sonradan):** yeni `kadraj/ui/widgets/surface.py` — indirici ve ayar sayfalarının (Basit ve Legend) kök zemini, tablo/liste ve kaydırma yüzeyleri tokenlardan widget düzeyinde boyanır; neden: `qss.py` içindeki `QAbstractScrollArea { background: transparent }` kuralı tablo/liste zeminini eziyor (çekirdek değiştirilmedi, öneri: o kuralı item görünümleri için düzeltmek).
**Testler:** `tests/downloader/test_legend_theme.py` (3) ve `tests/settings/test_legend_theme.py` (4) piksel ölçümü yapar (`tests/ui_pixels.py`: zemin, tablo gövdesi, WCAG kontrast ≥4,5:1); yüzey kapatılınca Legend testleri düşer (mutasyonla doğrulandı).
