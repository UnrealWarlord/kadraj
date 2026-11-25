"""``python main.py --selftest`` — ayrı süreçte, ekransız, ağ kapalı."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_main_selftest_passes_in_a_clean_process(tmp_path):
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "HOME": str(tmp_path / "home")}
    env.pop("KADRAJ_HOME", None)
    shots = tmp_path / "shots"
    proc = subprocess.run([sys.executable, str(ROOT / "main.py"), "--selftest", "--screenshots", str(shots)],
                          cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SELFTEST TAMAM" in proc.stdout and "HATA" not in proc.stdout
    assert (shots / "simple.png").is_file() and (shots / "legend.png").is_file()
    assert not (tmp_path / "home" / ".kadraj").exists()               # gerçek ev dizinine yazılmadı
