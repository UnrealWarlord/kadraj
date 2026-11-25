# KADRAJ

Yerel, yapay zekâ destekli video / ses / fotoğraf ve dizi-film prodüksiyon stüdyosu (Python + PySide6).
Adım raporları ve kararlar: `docs/steps/`, `docs/DECISIONS.md`.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python main.py --selftest          # ekransız sınama
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/core
.venv/bin/python main.py                     # uygulamayı aç
```

Uygulama iskeleti MIT lisanslı SoilClassPro'dan uyarlanmıştır (bkz. `LICENSE`).
