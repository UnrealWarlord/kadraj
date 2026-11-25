# KADRAJ · Adım kaydı

Her satır: `Adım · thread · durum · devir notu`. Commit'ler yalnızca yerel kayıt noktasıdır (push yok). Önceki adımın değişikliklerini görmek için dosyaları baştan okuma: `git show --stat <commit>` ve gerekirse `git show <commit> -- <dosya>`.

| Adım | Thread | Durum | Commit | Devir notu |
|---|---|---|---|---|
| 1 · Çekirdek iskelet ve sözleşmeler | Sonnet | ✓ kontrol edildi · tests/core 147 passed · selftest ✓ | efecbbb | `docs/steps/STEP_1.md` (yalnız `## DEVİR`) |
| 2 · Proje ve depolama | Haiku | ✓ kontrol edildi · tests/storage 97 passed | bfcf7fb | `docs/steps/STEP_2.md` (yalnız `## DEVİR`) |
| 3 · Güvenlik ve sistem | Sonnet | ✓ kontrol edildi · tests/security 165 passed · tümü 409 · selftest ✓ | 7afd23e | `docs/steps/STEP_3.md` (yalnız `## DEVİR`) |
| 4 · Medya İndirici | Haiku | ✓ kontrol edildi · tests/downloader 53 passed | 8a0173b | `docs/steps/STEP_4.md` (yalnız `## DEVİR`) |
| 5 · Ayarlar ekranı | Haiku | ✓ kontrol edildi · tests/settings 48 passed | 2b7baaa | `docs/steps/STEP_5.md` (yalnız `## DEVİR`) |
| 6 · Ses Kayıt Stüdyosu | Sonnet | ✓ kontrol edildi · tests/recorder 253 passed · tümü 765 · selftest ✓ | c28acea | `docs/steps/STEP_6.md` (yalnız `## DEVİR`) |
| 7 · Video Düzenleme | Sonnet | ✓ kontrol edildi · tests/editor 65 passed · tümü 881 · selftest ✓ (gerçek FFmpeg) | 6aff72a | `docs/steps/STEP_7.md` (yalnız `## DEVİR`) |
| 8a · AI motoru (kolay parça: prompt adaptörleri, stok, dışa aktarım, analitik) | Haiku | ✓ kontrol edildi · tests/ai_easy 51 passed | 69c1c25 | `docs/steps/STEP_8a.md` (yalnız `## DEVİR`) |
| 8b · AI motoru (zor parça: sağlayıcılar, Fotoğraf, Dizi/Film, düğümler, foley) | Sonnet | ✓ kontrol edildi · tests/ai 77 passed · tümü 958 · selftest ✓ | e29646e | `docs/steps/STEP_8b.md` (yalnız `## DEVİR`) |
| Düzeltme · varsayılan dil Türkçe, dilden bağımsız selftest | Sonnet | ✓ | 2f756ed | — |
| 8c · Legend analitik paneli + İndirici/Ayarlar kontrastı | Haiku | ✓ | cf6dca5 | `docs/steps/STEP_8c.md` |
| Son kontrol | Koordinatör | ✓ Docker (Python 3.12, ağ kapalı, Türkçe yerel ayar yok): 982 passed, selftest 34/34, 14 ekran iki modda · temiz `.venv` (Python 3.14, yalnız `requirements.txt`): 958 passed (düzeltmelerden önce) · gerçek keyring gidiş-dönüş ✓, kayıt kalmadı · doğrulama imajları ve venv silindi | — | — |
