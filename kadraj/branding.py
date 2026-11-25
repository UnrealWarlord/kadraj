"""Marka sabitleri.

Plan §0.1: "KADRAJ" yazısı ya da logosu geçen her yerde, sağ altında küçük
``from İnsibağ`` imzası bulunur ve bu imza yalnızca ``kadraj.ui.widgets.Lockup``
bileşeniyle çizilir. Bu yüzden "KADRAJ" sözcüğü arayüz metinlerinde (i18n
tabloları dahil) **geçmemelidir**; gerektiğinde ``Lockup`` kullanılır.

Bu dosya, ``Lockup``'ın kendisi ve işletim sisteminin kendi çizdiği pencere
başlığı gibi Lockup'ın çizemediği yerler için tek izinli sabit kaynağıdır.
``tests/core/test_brand_rule.py`` bu kuralı kaynak taramasıyla denetler.
"""
from __future__ import annotations

APP_NAME = "KADRAJ"
SIGNATURE = "from İnsibağ"

# Lockup oranları (plan §0.1): imza boyu = wordmark boyu x 0.30, en az 9 px,
# %60 opaklık, sağ kenarı "J" ile hizalı. Wordmark: Archivo 800, genişlik %125,
# harf aralığı 0.12 em.
SIGNATURE_RATIO = 0.30
SIGNATURE_MIN_PX = 9
SIGNATURE_OPACITY = 0.60
WORDMARK_TRACKING_EM = 0.12
WORDMARK_STRETCH = 125


def window_title(suffix: str = "") -> str:
    """İşletim sistemi başlık çubuğu için metin (Lockup çizemez).

    İmza metne gömülüdür; böylece "KADRAJ" geçen hiçbir yer imzasız kalmaz.
    """
    base = f"{APP_NAME} · {SIGNATURE}"
    return f"{base} — {suffix}" if suffix else base
