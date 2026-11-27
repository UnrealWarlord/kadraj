"""Sistem izleyici (``SysmonAPI``; sözleşme: ``core.contracts`` §9).

``sample(path)`` → ``{"cpu", "ram_used", "ram_total", "gpu", "vram_used", "vram_total", "gpu_name", "disk_free",
"disk_total", "api_ping"}``; olmayan değer ``None`` (arayüz "—" gösterir). psutil yoksa (Android paketi) CPU ``None``;
disk standart kütüphaneyle (``shutil.disk_usage``), bellek Linux/Android'de ``/proc/meminfo``'dan okunur.
``available_keys()``: bu cihazda ölçülebilen satırlar (Android'de işlemci ve ekran kartı yok, M21).

GPU: ``pynvml`` (paket adı ``nvidia-ml-py``) isteğe bağlıdır; kurulu değilse, NVIDIA sürücüsü yoksa ya da başlatma
başarısızsa **sessizce** ``None`` döner (bir kez denenir, sonuç önbelleğe alınır). Örnekleme hiçbir zaman ağa çıkmaz ve
istisna atmaz. ``api_ping`` son "Bağlantıyı Test Et" gecikmesidir (``record_ping``; ``ProviderRegistry.on_ping`` besler).
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .. import device

_KEYS = ("cpu", "ram_used", "ram_total", "gpu", "vram_used", "vram_total", "gpu_name", "disk_free", "disk_total",
         "api_ping")
#: Sistem panelinin satırları (``ui/shell/legend.SystemPanel``).
PANEL_KEYS = ("cpu", "ram", "gpu", "vram", "disk", "ping")
MEMINFO = Path("/proc/meminfo")


def available_keys() -> Tuple[str, ...]:
    """Bu cihazda ölçülebilen panel satırları. Android uygulamaları işlemci yükünü okuyamaz (``/proc/stat`` kapalı)
    ve NVIDIA ekran kartı yoktur: orada yalnız bellek, disk ve API gecikmesi."""
    if device.is_android():
        return ("ram", "disk", "ping")
    return PANEL_KEYS


def meminfo(text: str) -> Optional[Tuple[int, int]]:
    """``/proc/meminfo`` metninden (kullanılan, toplam) bayt; okunamazsa ``None``."""
    values: Dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[name.strip()] = int(parts[0]) * (1024 if parts[1:2] == ["kB"] else 1)
    total = values.get("MemTotal")
    free = values.get("MemAvailable", values.get("MemFree"))
    if not total or free is None:
        return None
    return max(0, total - free), total


class SystemMonitor:
    def __init__(self, *, nvml: Any = None, psutil_module: Any = None, ping_ttl: float = 600.0) -> None:
        self._nvml = nvml                       # testler sahte pynvml verir
        self._nvml_state = "unknown" if nvml is None else "ready"
        self._handle: Any = None
        self._gpu_name: Optional[str] = None
        self._ping: Optional[float] = None
        self._ping_at = 0.0
        self._ping_ttl = ping_ttl
        self._psutil = psutil_module
        if self._psutil is None:
            try:
                import psutil
                self._psutil = psutil
                psutil.cpu_percent(interval=None)       # ilk çağrı 0.0 döner; ısındır
            except Exception:
                self._psutil = False
        if nvml is not None:
            self._init_nvml()

    # -- GPU --------------------------------------------------------------------------
    def _init_nvml(self) -> None:
        try:
            if self._nvml is None:
                import pynvml  # type: ignore
                self._nvml = pynvml
            self._nvml.nvmlInit()
            self._handle = self._nvml.nvmlDeviceGetHandleByIndex(0)
            name = self._nvml.nvmlDeviceGetName(self._handle)
            self._gpu_name = name.decode() if isinstance(name, bytes) else str(name)
            self._nvml_state = "ready"
        except Exception:
            self._nvml_state = "absent"
            self._handle = None

    def _gpu(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"gpu": None, "vram_used": None, "vram_total": None, "gpu_name": None}
        if self._nvml_state == "unknown":
            self._init_nvml()
        if self._nvml_state != "ready" or self._handle is None:
            return out
        try:
            util = self._nvml.nvmlDeviceGetUtilizationRates(self._handle)
            mem = self._nvml.nvmlDeviceGetMemoryInfo(self._handle)
            out.update(gpu=float(util.gpu), vram_used=int(mem.used), vram_total=int(mem.total),
                       gpu_name=self._gpu_name)
        except Exception:
            self._nvml_state = "absent"                # sürücü yok oldu/hata: bir daha deneme
        return out

    @property
    def gpu_available(self) -> bool:
        if self._nvml_state == "unknown":
            self._init_nvml()
        return self._nvml_state == "ready"

    # -- API ping ---------------------------------------------------------------------------
    def record_ping(self, ms: Optional[float]) -> None:
        self._ping = None if ms is None else float(ms)
        self._ping_at = time.monotonic()

    # -- örnek --------------------------------------------------------------------------------
    def sample(self, path: Optional[str] = None) -> Dict[str, Any]:
        out: Dict[str, Any] = {k: None for k in _KEYS}
        ps = self._psutil
        if ps:
            try:
                out["cpu"] = float(ps.cpu_percent(interval=None))
                vm = ps.virtual_memory()
                out["ram_used"], out["ram_total"] = int(vm.used), int(vm.total)
            except Exception:
                pass
            try:
                du = ps.disk_usage(path if path and Path(path).exists() else str(Path.home()))
                out["disk_free"], out["disk_total"] = int(du.free), int(du.total)
            except Exception:
                pass
        else:                                          # psutil yok (Android): standart kütüphane ve /proc
            try:
                du = shutil.disk_usage(path if path and Path(path).exists() else str(Path.home()))
                out["disk_free"], out["disk_total"] = int(du.free), int(du.total)
            except Exception:
                pass
            try:
                ram = meminfo(MEMINFO.read_text(encoding="ascii", errors="replace"))
            except Exception:
                ram = None
            if ram is not None:
                out["ram_used"], out["ram_total"] = ram
        out.update(self._gpu())
        if self._ping is not None and time.monotonic() - self._ping_at <= self._ping_ttl:
            out["api_ping"] = self._ping
        return out
