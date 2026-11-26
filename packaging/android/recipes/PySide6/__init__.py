"""KADRAJ · PySide6 tarifi (python-for-android). pyside6-android-deploy'un ürettiği tarifin yerine geçer.

Üretilen tarif tekerleğin tamamını iki kez paketler: site-packages'a (Qt/lib 150 MB, QML, çeviriler, tüm eklentiler)
ve yerel kütüphane klasörüne (bütün Qt modülleri). Deneme APK'sı bu yüzden 170 MB'tı ve ilk açılışta yüzlerce MB
açılıyordu. Bu tarif yalnız uygulamanın kullandığını paketler:

* site-packages/PySide6: Python dosyaları + uygulamanın Qt modüllerinin bağları (Qt/, include/, jar/, .pyi yok;
  Qt kütüphaneleri Android'de yerel kütüphane klasöründen yüklenir).
* yerel kütüphane klasörü: bu modüllerin, açılışta yüklenen kütüphanelerin ve seçilen eklentilerin ELF bağımlılık
  kapanışı (DT_NEEDED; tekerlekte olmayan adlar sistem kütüphanesidir).

Ayarlar ``build_apk.sh``'in yanına yazdığı ``kadraj.json``'dadır: tekerlek yolu, ``--qt-libs`` ve ``--load-local-libs``
listeleri (deploy aracının buildozer.spec'e yazdığı) ve ek eklentiler (ör. ``imageformats/qjpeg``).
"""
from __future__ import annotations

import json
import shutil
import struct
import zipfile
from pathlib import Path, PurePosixPath

from pythonforandroid.logger import info
from pythonforandroid.recipe import PythonRecipe

CONFIG = Path(__file__).with_name("kadraj.json")
#: site-packages/PySide6 altında çalışma zamanında gerekmeyen klasörler
SKIP_DIRS = {"Qt", "include", "jar", "lib", "QtAsyncio", "typesystems", "glue", "scripts"}
QT_LIB = "PySide6/Qt/lib/"
QT_PLUGINS = "PySide6/Qt/plugins/"


def elf_needed(data: bytes) -> list:
    """64 bit little-endian ELF'in DT_NEEDED adları (program başlıklarından; bölüm başlığı gerekmez)."""
    if len(data) < 64 or data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        return []
    (phoff,) = struct.unpack_from("<Q", data, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
    loads, dynamic = [], None
    for i in range(phnum):
        p_type, _flags, offset, vaddr, _paddr, filesz, _memsz, _align = struct.unpack_from(
            "<IIQQQQQQ", data, phoff + i * phentsize)
        if p_type == 1:                                   # PT_LOAD
            loads.append((vaddr, offset, filesz))
        elif p_type == 2:                                 # PT_DYNAMIC
            dynamic = (offset, filesz)
    if dynamic is None:
        return []
    needed, strtab = [], None
    for j in range(dynamic[1] // 16):
        tag, val = struct.unpack_from("<qQ", data, dynamic[0] + 16 * j)
        if tag == 0:                                      # DT_NULL
            break
        if tag == 1:                                      # DT_NEEDED
            needed.append(val)
        elif tag == 5:                                    # DT_STRTAB (sanal adres → dosya konumu)
            strtab = next((val - v + o for v, o, size in loads if v <= val < v + size), None)
    if strtab is None:
        return []
    return [data[strtab + n:data.index(b"\0", strtab + n)].decode() for n in needed]


def plan(names: list, config: dict, read) -> tuple:
    """Tekerlek üyelerinden (``names``) ne paketleneceği: (site-packages üyeleri, yerel klasöre {ad: üye}).

    ``read(üye) -> bytes`` yalnız kapanış için okunan kütüphanelerde çağrılır. Bulunamayan istenen kütüphane hata verir.
    """
    abi = config.get("abi", "arm64-v8a")
    modules = [m.strip() for m in config["qt_libs"] if m.strip()]
    bindings = {f"PySide6/Qt{m}.abi3.so" for m in modules}

    site = []
    for name in names:
        p = PurePosixPath(name)
        if p.parts[0] == "PySide6":
            if len(p.parts) > 2 and p.parts[1] in SKIP_DIRS:
                continue
            if len(p.parts) == 2 and p.name.startswith("Qt") and p.name.endswith(".so") and name not in bindings:
                continue
            if p.suffix == ".pyi" or p.name in ("py.typed", "libpyside6qml.abi3.so"):
                continue
        site.append(name)

    by_file = {}                                          # yerel ad → tekerlek üyesi (Qt/lib ve düzleştirilmiş eklentiler)
    for name in names:
        if name.endswith(".so") and (name.startswith(QT_LIB) or name.startswith(QT_PLUGINS)):
            by_file.setdefault(PurePosixPath(name).name, name)

    def lib_file(short: str) -> str:
        for cand in (f"lib{short}_{abi}.so", f"lib{short}.so"):
            if cand in by_file:
                return cand
        raise RuntimeError(f"[KADRAJ] tekerlekte yok: {short}")

    roots = [f"libQt6{m}_{abi}.so" for m in modules]
    roots += [lib_file(s.strip()) for s in config.get("load_local_libs", []) if s.strip()]
    roots += [lib_file("plugins_" + p.strip().replace("/", "_")) for p in config.get("plugins", []) if p.strip()]
    missing = [r for r in roots if r not in by_file]
    if missing:
        raise RuntimeError(f"[KADRAJ] tekerlekte yok: {missing}")

    present = set(names)
    queue = [b for b in sorted(bindings) if b in present] + [by_file[r] for r in roots]
    native, seen = {}, set()                              # yerel ad → tekerlek üyesi
    while queue:
        member = queue.pop()
        if member in seen:
            continue
        seen.add(member)
        native[PurePosixPath(member).name] = member
        queue.extend(by_file[dep] for dep in elf_needed(read(member)) if dep in by_file)
    return site, native


def safe_extract(zf: zipfile.ZipFile, member: str, target: Path) -> None:
    """Üyeyi ``target`` altına çıkarır; klasör dışına taşan yolları reddeder."""
    out = (target / member).resolve()
    if not out.is_relative_to(target.resolve()):
        raise RuntimeError(f"[KADRAJ] klasör dışına çıkan tekerlek üyesi: {member}")
    zf.extract(member, target)


class PySideRecipe(PythonRecipe):
    version = "6.12.0"
    depends = ["shiboken6"]
    call_hostpython_via_targetpython = False
    install_in_hostpython = False

    def build_arch(self, arch):
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        config["abi"] = arch.arch
        libs_dir = Path(self.ctx.get_libs_dir(arch.arch))
        site_dir = Path(self.ctx.get_python_install_dir(arch.arch))
        libs_dir.mkdir(parents=True, exist_ok=True)

        info("libc++_shared.so NDK'den kopyalanıyor (açılışta yüklenir)")
        libcpp = Path(f"{self.ctx.ndk.sysroot_lib_dir}/{arch.command_prefix}/libc++_shared.so")
        shutil.copyfile(libcpp, libs_dir / "libc++_shared.so")

        with zipfile.ZipFile(config["wheel"]) as zf:
            names = zf.namelist()
            site, native = plan(names, config, zf.read)
            info(f"[KADRAJ] site-packages: {len(site)} dosya (tekerlekte {len(names)})")
            for member in site:
                safe_extract(zf, member, site_dir)
            total = 0
            for base, member in sorted(native.items()):
                with zf.open(member) as src, open(libs_dir / base, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                total += zf.getinfo(member).file_size
                info(f"[KADRAJ] yerel: {base}")
            info(f"[KADRAJ] yerel kütüphane: {len(native)} dosya, {total // (1024 * 1024)} MB")
            # pyside6-android-deploy'un tarifi gibi: bağ kütüphanesi yerel klasörde de bulunur
            for extra in ("PySide6/libpyside6.abi3.so",):
                with zf.open(extra) as src, open(libs_dir / PurePosixPath(extra).name, "wb") as dst:
                    shutil.copyfileobj(src, dst)


recipe = PySideRecipe()
