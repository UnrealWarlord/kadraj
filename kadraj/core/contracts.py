"""KADRAJ sözleşmeleri — iki thread'in (Sonnet / Haiku) ortak dili.

Bu dosya **sadece Adım 1'de** değiştirilir (plan §0.6). Burada tanımlanan
imzalar sabittir: gövdeler sonraki adımlarda dolar ama imza değişmez.
Dosyada iş mantığı yoktur; yalnızca tipler, Protocol'ler, sabitler ve küçük
yardımcı sınıflar bulunur. Qt'ye bağımlılık yalnızca ``TYPE_CHECKING`` altındadır;
yani bu dosya Qt olmadan da içe aktarılır (testler ve araçlar için).

İçindekiler
-----------
1. Genel sabitler: ``UiMode``, ``Accent``, ``Topics``, klasör adları
2. Modül sözleşmesi: ``ModuleSpec``, ``PageFactory``, ``Service`` / ``ServiceBase``
3. İş kuyruğu: ``Job``, ``JobStatus``, ``JobContext``, ``JobQueueAPI``
4. Sağlayıcılar ve kasa: ``Provider``, ``PingResult``, ``VaultAPI``
5. Akıllı Hata: ``SmartError``, ``SmartAction``, ``ErrorRule``, ``Translator``
6. Depolama kancaları: ``RecentEntry``, ``RecentAPI``, ``WizardAnswers``
7. ``ContextAPI`` — sayfaların ve servislerin gördüğü uygulama bağlamı
8. **Registry modülleri nasıl keşfeder** (kural özeti)
9. **Adım 3 eklemeleri** (yalnızca ekleme): ``VaultAdminAPI``, ``ProviderRegistryAPI``, ``JobControlAPI``,
   ``AutosaveAPI``, ``SnapshotAPI``, ``SysmonAPI`` ve yardımcı veri tipleri

Altın kurallar (plan §0)
------------------------
* **Durum widget'ta tutulmaz.** Sayfa fabrikaları *her çağrıldığında* yeni bir
  widget üretir; Basit ↔ Legend geçişinde sayfa atılıp yeniden kurulabilir. Bütün
  durum (seçim, playhead, form değerleri, ilerleme) ``Service`` ya da proje
  modelindedir; sayfa yalnızca ona bağlanır ve ``bus`` olaylarını dinler.
* **Ağ çağrıları zaman aşımıyla** sarılır ve hata, ``SmartError`` olarak yüzeye
  çıkar; hiçbir yerde yakalanmamış istisna uygulamayı düşürmez.
* **Otomasyonlar kendiliğinden uygulanmaz.** ``Service`` kullanıcı tetiklemeden
  sessizlik kesme, mastering, ducking vb. çalıştırmaz; hepsi geri alınabilir
  (``ctx.undo``).
* **Ses çıktısı yalnızca ``.wav``.**
* **"KADRAJ" yazısı yalnızca ``Lockup`` ile** (``kadraj.ui.widgets.Lockup``).
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import (TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional,
                    Protocol, Sequence, Tuple, runtime_checkable)

if TYPE_CHECKING:  # Qt yalnızca tip denetimi için
    from PySide6.QtGui import QUndoStack
    from PySide6.QtWidgets import QWidget

CONTRACT_VERSION = 1


# ===========================================================================
# 1. Genel sabitler
# ===========================================================================
class UiMode(str, enum.Enum):
    """Arayüz modu. Değerler ayarlarda ve proje dosyasında saklanan dizelerdir."""
    SIMPLE = "simple"   # "Basit Mod" — pastel, yuvarlak, görev kartları
    LEGEND = "legend"   # "Legend Mode" — koyu, keskin, dock'lu çalışma alanları

    @classmethod
    def coerce(cls, value: "str | UiMode | None") -> "UiMode":
        """Bilinmeyen/boş değeri ``SIMPLE``'a düşürür; asla istisna atmaz."""
        try:
            return cls(value)
        except ValueError:
            return cls.SIMPLE


class Accent(str, enum.Enum):
    """Modül vurgu rengi. Gerçek renk, tema modunda çözülür
    (``kadraj.ui.theme.tokens``): Basit'te pastel, Legend'de canlı karşılığı."""
    PEACH = "peach"          # Basit: şeftali      · Legend: turuncu  (İndirici)
    LAVENDER = "lavender"    # Basit: lavanta      · Legend: mor      (Ses)
    MINT = "mint"            # Basit: nane         · Legend: altın    (Kurgu)
    BUTTER = "butter"        # Basit: tereyağı     · Legend: camgöbeği (Fotoğraf)
    PINK = "pink"            # Basit: pembe        · Legend: pembe    (Dizi/Film)
    NEUTRAL = "neutral"      # Ayarlar vb.


#: Legend çalışma alanları (kimlikler). ``ModuleSpec.workspace`` bunlardan biridir.
WORKSPACES: Tuple[str, ...] = ("project", "edit", "studio", "capture", "system")


class Topics:
    """``ctx.bus`` olay konuları. Yük (payload) tipleri yanlarında."""
    MODE_CHANGED = "ui.mode_changed"            # str ("simple"|"legend")
    LANGUAGE_CHANGED = "ui.language_changed"    # str ("tr"|"en")
    A11Y_CHANGED = "ui.a11y_changed"            # dict {large_text, high_contrast, reduced_motion}
    NAVIGATE = "ui.navigate"                    # dict {"module": id}  (ana pencere sayfayı açar)
    NOTIFY = "ui.notify"                        # dict {"text": str, "level": "info|warn|error"}
    PROJECT_OPENED = "project.opened"           # str (proje klasörü yolu)
    PROJECT_CLOSED = "project.closed"           # None
    PROJECT_DIRTY = "project.dirty"             # bool
    PROJECT_SAVED = "project.saved"             # str (proje dosyası yolu)
    AUTOSAVE_STATE = "autosave.state"           # dict {"state": "idle|saving|saved|error", "at": epoch}
    REWIND_REQUESTED = "autosave.rewind"        # dict {"minutes": 10}  — "10 Dk Önceye Döndür"
    JOB_ADDED = "job.added"                     # Job
    JOB_UPDATED = "job.updated"                 # Job
    ERROR = "error.raised"                      # SmartError
    SYSMON = "sys.sample"                       # dict {"cpu": %, "ram_used": B, "ram_total": B, "gpu": %|None, ...}
    PROJECT_RESTORED = "project.restored"       # dict {"reason": "rewind|snapshot|undo_rewind", "label": str}  (Adım 3)


# --- Proje klasör düzeni (plan §3) ------------------------------------------
PROJECT_FILE_NAME = "proje.kadraj"
PROJECT_FORMAT = "kadraj-project"
PROJECT_FORMAT_VERSION = 1
PROJECT_SUBFOLDERS: Tuple[str, ...] = (
    "01_Downloads", "02_Audio_WAV", "03_AI_Images", "04_AI_Scenes",
    "05_Scripts", "06_Proxies", "07_Exports",
)
HIDDEN_DIR = ".kadraj"
HIDDEN_SUBDIRS: Tuple[str, ...] = ("autosave", "snapshots", "cache")
DB_RELATIVE = f"{HIDDEN_DIR}/proje.db"

#: Ses çıktısı yalnızca WAV (plan §0.4). Basit sabit: 48 kHz / 24-bit.
AUDIO_OUTPUT_EXT = ".wav"
SIMPLE_SAMPLE_RATE = 48000
LEGEND_SAMPLE_RATES: Tuple[int, ...] = (44100, 48000, 88200, 96000)


# ===========================================================================
# 2. Modül sözleşmesi
# ===========================================================================
@runtime_checkable
class PageFactory(Protocol):
    """Sayfa fabrikası: her çağrıda **yeni** bir ``QWidget`` döner.

    ``page = spec.simple_page(ctx)``. Sayfa durumunu *kendinde* tutmaz; servise
    (``ctx.service(spec.id)``) bağlanır. Mod değişince ana pencere sayfayı atar
    ve ilgili modun fabrikasını yeniden çağırır. Fabrika çağrısı pahalı iş
    yapmamalıdır (ağ/disk taraması yok); ağır iş servise ve ``ctx.jobs``'a aittir.
    """

    def __call__(self, ctx: "ContextAPI") -> "QWidget": ...


@runtime_checkable
class Service(Protocol):
    """Bir modülün durum ve iş mantığı katmanı (Qt widget'ı DEĞİLDİR).

    Yaşam döngüsü: ``ctx.service(id)`` ilk istendiğinde ``service_factory(ctx)``
    ile üretilir ve ``start(ctx)`` çağrılır; uygulama kapanırken ``stop()``.
    ``start``/``stop`` hızlı olmalı, istisna atmamalıdır (atarsa kayıt defteri
    ``SmartError``'a çevirip yutar). Servis ``QObject`` olabilir (sinyal için).
    """

    name: str

    def start(self, ctx: "ContextAPI") -> None: ...
    def stop(self) -> None: ...


class ServiceBase:
    """``Service`` için boş-uygulamalı yardımcı taban sınıf (kalıtım isteğe bağlı)."""
    name: str = ""

    def __init__(self) -> None:
        self.ctx: Optional["ContextAPI"] = None

    def start(self, ctx: "ContextAPI") -> None:
        self.ctx = ctx

    def stop(self) -> None:
        self.ctx = None


ServiceFactory = Callable[["ContextAPI"], Service]


@dataclass(frozen=True)
class ModuleSpec:
    """Bir modülün kayıt defterine verdiği özet.

    ``kadraj/modules/<ad>/module.py`` içindeki ``register()`` bunu döndürür
    (bkz. bölüm 8). ``simple_page`` / ``legend_page`` ``None`` ise ana pencere o
    mod için "yakında" yer tutucusu gösterir.
    """
    id: str                                   # küçük harf, paket adıyla aynı: "downloader"
    title_key: str                            # i18n anahtarı; mod çiftli ise "simple.<k>"/"legend.<k>" aranır
    order: int                                # ana ekranda ve çalışma alanında sıra (küçük önce)
    accent: Accent = Accent.NEUTRAL
    icon: str = "dot"                         # kadraj.ui.widgets.icons içindeki ad
    desc_key: str = ""                        # Basit ana ekran kartının açıklaması (i18n)
    chip: str = ""                            # kartta küçük rozet metni (örn. ".wav")
    shortcut: str = ""                        # Legend "Hızlı Başlat" kısayolu (örn. "Ctrl+1")
    workspace: str = "capture"                # WORKSPACES içinden; Legend'de hangi çalışma alanında açılır
    simple_page: Optional[PageFactory] = None
    legend_page: Optional[PageFactory] = None
    service_factory: Optional[ServiceFactory] = None
    requires: Tuple[str, ...] = ()            # harici ikililer: ("ffmpeg",) — eksikse kart uyarı gösterir
    strings: Optional[Mapping[str, Mapping[str, str]]] = None   # {"tr": {...}, "en": {...}}; anahtarlar "<id>.<ad>"

    def validate(self) -> List[str]:
        """Sözleşmeye aykırılıkların listesi (boş = geçerli). Kayıt defteri çağırır."""
        problems: List[str] = []
        if not self.id or not self.id.isidentifier() or self.id != self.id.lower():
            problems.append(f"id geçersiz: {self.id!r} (küçük harfli Python tanımlayıcısı olmalı)")
        if not self.title_key:
            problems.append("title_key boş")
        if self.workspace not in WORKSPACES:
            problems.append(f"workspace bilinmiyor: {self.workspace!r}")
        if self.strings:
            for lang, table in self.strings.items():
                for key in table:
                    if not key.startswith(self.id + "."):
                        problems.append(
                            f"strings[{lang}] anahtarı '{self.id}.' ile başlamalı: {key!r}")
        return problems


# ===========================================================================
# 3. İş kuyruğu (gövde: Adım 3 — core/jobs.py)
# ===========================================================================
class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    """Kalıcı iş kaydı (SQLite ``jobs`` tablosunun satırı, plan §3)."""
    kind: str                                   # "download", "render", "whisper", ...
    id: Optional[int] = None                    # DB atar
    title: str = ""
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0                       # 0..1
    payload: Dict[str, Any] = field(default_factory=dict)   # JSON-uyumlu
    error: Optional[str] = None                 # sade Türkçe özet (SmartError.summary)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    @property
    def finished(self) -> bool:
        return self.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)


class JobContext(Protocol):
    """İş işleyicisine verilen yardımcı nesne (iş parçacığında çalışır)."""

    def report(self, progress: float, message: str = "") -> None:
        """İlerleme bildir (0..1). Thread-güvenlidir."""

    def is_cancelled(self) -> bool:
        """İptal istendi mi? İşleyici düzenli aralıklarla bakmalıdır."""


JobHandler = Callable[[Job, JobContext], Any]


@runtime_checkable
class JobQueueAPI(Protocol):
    """``ctx.jobs``. Değişiklikler ``Topics.JOB_ADDED`` / ``JOB_UPDATED`` ile duyurulur.

    İşleyici içinde atılan istisna ``translate(exc)`` ile ``SmartError``'a çevrilir,
    iş ``FAILED`` olur; uygulama düşmez. Bitince masaüstü bildirimi (isteğe bağlı)
    kuyruk tarafından gönderilir.
    """

    def register_handler(self, kind: str, handler: JobHandler) -> None: ...
    def submit(self, kind: str, payload: Optional[Mapping[str, Any]] = None,
               title: str = "") -> Job: ...
    def cancel(self, job_id: int) -> bool: ...
    def get(self, job_id: int) -> Optional[Job]: ...
    def list(self, status: Optional[JobStatus] = None) -> List[Job]: ...


# ===========================================================================
# 4. Sağlayıcılar ve kasa (gövde: Adım 3 — ai_engine/providers/base.py, storage/vault.py)
# ===========================================================================
class ProviderKind(str, enum.Enum):
    LLM = "llm"
    IMAGE = "image"
    VIDEO = "video"
    TTS = "tts"
    STT = "stt"
    STOCK = "stock"      # Pexels / Pixabay / Freesound gibi arşivler
    LOCAL = "local"      # Ollama / ComfyUI / Whisper — anahtarsız, yerel


@dataclass(frozen=True)
class PingResult:
    """"Bağlantıyı Test Et" sonucu. ``ping`` asla istisna atmaz; hata burada döner."""
    ok: bool
    latency_ms: Optional[float] = None
    message: str = ""                           # sade Türkçe, tek cümle
    error: Optional["SmartError"] = None        # ok=False ise doldurulur


@runtime_checkable
class Provider(Protocol):
    """Bir AI/arşiv sağlayıcısının ortak yüzü (``ai_engine/providers/*``)."""

    id: str                                     # "anthropic", "ollama", ...
    display_name: str
    kind: ProviderKind
    needs_key: bool                             # False → yerel / anahtarsız
    local: bool

    def is_configured(self, vault: "VaultAPI") -> bool:
        """Anahtar var mı (ya da yerel servis ayarlı mı)? Ağa çıkmaz."""

    def ping(self, vault: "VaultAPI", timeout: float = 5.0) -> PingResult:
        """Zaman aşımlı hafif istek. Çevrimdışıysa ``ok=False`` + ``SmartError``."""


@runtime_checkable
class VaultAPI(Protocol):
    """Şifreli API kasası (``ctx.vault``). Gövde Adım 3'te; imza şimdi sabit.

    Ayarlar ekranı (Adım 5) yalnızca **maskeli** okur: ``masked()`` ``"sk-••••3f9a"``
    gibi bir dize döner; düz anahtar yalnızca ``get_secret`` ile, sağlayıcı
    çağrısı sırasında alınır ve asla loglanmaz/ekrana yazılmaz.
    Kasa şifrelenmiş (Fernet; anahtar OS keyring'de) ``~/.kadraj/vault.bin`` dosyasıdır.
    Adım 3 gelene dek ``ctx.vault`` bellek-içi bir yer tutucudur
    (``core.stubs.InMemoryVault``, ``is_placeholder = True``; diske yazmaz).
    """

    is_placeholder: bool

    def is_available(self) -> bool:
        """Şifreleme arka ucu (keyring + cryptography) kullanılabilir mi?"""

    def providers(self) -> List[str]:
        """Kasada kaydı olan sağlayıcı kimlikleri."""

    def has_secret(self, provider_id: str) -> bool: ...
    def masked(self, provider_id: str) -> str:
        """Maskeli gösterim; kayıt yoksa boş dize."""

    def set_secret(self, provider_id: str, value: str) -> None:
        """Kaydet (üzerine yazar). Boş değer ``delete_secret`` gibi davranmaz; ``ValueError``."""

    def get_secret(self, provider_id: str) -> Optional[str]: ...
    def delete_secret(self, provider_id: str) -> bool: ...


# ===========================================================================
# 5. Akıllı Hata (gövde: core/errors.py; sözlük: Adım 2 — core/error_catalog.py)
# ===========================================================================
class Severity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class SmartAction:
    """Hata kartındaki bir düğme. ``kind`` kartı çizen kabuk tarafından yorumlanır.

    * ``retry``         — son işlemi yeniden dene (``payload`` işleyiciye geçer)
    * ``open_settings`` — ``payload["section"]`` ile Ayarlar'ı aç
    * ``open_url``      — ``payload["url"]`` (kullanıcı onayıyla, tarayıcıda)
    * ``copy``          — ``payload["text"]`` panoya
    * ``custom``        — ``payload["topic"]`` bus konusunu yayınla
    """
    id: str
    label: str                                  # sade Türkçe (i18n'den çözülmüş)
    kind: str = "custom"
    payload: Mapping[str, Any] = field(default_factory=dict)


class SmartError(Exception):
    """Kullanıcıya sade Türkçe gösterilen hata (Akıllı Hata Kartı'nın verisi).

    Alanlar kartın bölümlerine denk gelir: ``title`` (başlık), ``summary``
    (ne oldu?), ``cause`` (neden olmuş olabilir?), ``steps`` (ne yapmalı?),
    ``actions`` (düğmeler), ``technical`` (katlanır "ayrıntılar" — ham ileti,
    komut çıktısı). ``code`` kararlı bir makine kimliğidir (örn.
    ``"net.offline"``, ``"ffmpeg.missing"``, ``"disk.full"``).
    """

    def __init__(self, code: str, title: str, summary: str = "", *,
                 cause: str = "", steps: Sequence[str] = (),
                 actions: Sequence[SmartAction] = (),
                 severity: Severity = Severity.ERROR,
                 technical: str = "", retryable: bool = False,
                 source: str = "") -> None:
        super().__init__(summary or title)
        self.code = code
        self.title = title
        self.summary = summary
        self.cause = cause
        self.steps: Tuple[str, ...] = tuple(steps)
        self.actions: Tuple[SmartAction, ...] = tuple(actions)
        self.severity = severity
        self.technical = technical
        self.retryable = retryable
        self.source = source                    # modül/sağlayıcı kimliği (isteğe bağlı)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code, "title": self.title, "summary": self.summary,
            "cause": self.cause, "steps": list(self.steps),
            "actions": [{"id": a.id, "label": a.label, "kind": a.kind,
                         "payload": dict(a.payload)} for a in self.actions],
            "severity": self.severity.value, "technical": self.technical,
            "retryable": self.retryable, "source": self.source,
        }


#: ``translate`` bağlamı: serbest anahtarlı sözlük. Bilinen anahtarlar:
#: ``tool`` ("ffmpeg"|"yt-dlp"|"http"|"cuda"|"disk"|"audio"|...), ``stderr`` (str),
#: ``returncode`` (int), ``url`` (str), ``path`` (str), ``provider`` (str),
#: ``status`` (HTTP durum kodu, int), ``source`` (modül kimliği).
ErrorContext = Mapping[str, Any]


@dataclass(frozen=True)
class ErrorRule:
    """Sözlük kuralı. Haiku Adım 2'de ``core/error_catalog.py`` içinde ``RULES``
    listesini bu tipte doldurur (FFmpeg, yt-dlp, HTTP, CUDA, disk).

    ``matches`` hızlı ve yan etkisiz olmalıdır; ``build`` ``SmartError`` döner.
    ``priority`` küçük olan önce denenir; ilk eşleşen kazanır.
    """
    id: str
    matches: Callable[[BaseException, ErrorContext], bool]
    build: Callable[[BaseException, ErrorContext], SmartError]
    priority: int = 100


@runtime_checkable
class Translator(Protocol):
    """``kadraj.core.errors.translate`` imzası."""

    def __call__(self, exc: BaseException,
                 context: Optional[ErrorContext] = None) -> SmartError:
        """Her istisnayı ``SmartError``'a çevirir; asla istisna atmaz, eşleşme yoksa genel kart döner."""


# ===========================================================================
# 6. Depolama kancaları (Adım 2 — Haiku şeridi)
# ===========================================================================
@dataclass(frozen=True)
class RecentEntry:
    """Son projeler listesindeki satır (``~/.kadraj/recent.json``)."""
    path: str                                   # proje KLASÖRÜ (içinde proje.kadraj var)
    name: str
    last_opened: float = 0.0                    # epoch saniye
    kind: str = "project"


@runtime_checkable
class RecentAPI(Protocol):
    """``ctx.recent``. Gövde: ``kadraj/storage/recent.py`` (Adım 2).

    Dosya biçimi (``paths.recent_file()``):
    ``{"version": 1, "recent": [{"path", "name", "last_opened", "kind"}]}``;
    en yeni başta, en çok ``MAX_RECENT`` (8) kayıt; diskte olmayan yollar
    ``get_recent`` sırasında sessizce ayıklanır. Bozuk dosya → boş liste.
    Adım 2 gelene dek kabuk ``core.context`` içindeki küçük bir yedek
    uygulamayı kullanır.
    """

    def get_recent(self) -> List[RecentEntry]: ...
    def add_recent(self, path: str, name: Optional[str] = None) -> None: ...
    def forget(self, path: str) -> None: ...
    def clear(self) -> None: ...


MAX_RECENT = 8

#: Sihirbaz görev kimlikleri (tuval: Onboarding)
WIZARD_TASKS: Tuple[str, ...] = ("dl", "rec", "edit", "photo", "film", "unsure")
WIZARD_PLACES: Tuple[str, ...] = ("videos", "desktop", "custom")
WIZARD_API: Tuple[str, ...] = ("yes", "how", "skip")


@dataclass
class WizardAnswers:
    """3 soruluk ilk açılış sihirbazının cevapları.

    Arayüz (Adım 1) bunu toplar ve ``core.settings.apply_wizard_answers`` ile
    ayarlara yazar. Veri tarafı (Adım 2): ``storage.folders`` kök klasörü hazırlar
    (``root_path`` var olmayabilir, oluşturulur), ``disk`` boş alan hesaplar.
    """
    tasks: Tuple[str, ...] = ("dl", "edit")         # Soru 1: ne yapmak istiyorsun? (çoklu)
    place: str = "videos"                           # Soru 2: kayıt yeri (WIZARD_PLACES)
    root_path: str = ""                             # place == "custom" ise seçilen klasör
    api: str = "skip"                               # Soru 3: API anahtarı (WIZARD_API)

    def normalized(self) -> "WizardAnswers":
        tasks = tuple(t for t in self.tasks if t in WIZARD_TASKS) or ("unsure",)
        place = self.place if self.place in WIZARD_PLACES else "videos"
        api = self.api if self.api in WIZARD_API else "skip"
        return WizardAnswers(tasks, place, self.root_path, api)


# ===========================================================================
# 7. Uygulama bağlamı
# ===========================================================================
@runtime_checkable
class SettingsAPI(Protocol):
    """``ctx.settings`` — ``~/.kadraj/settings.json``. Anahtarlar noktalı yol olabilir
    (``"a11y.large_text"``). ``set`` anında diske atomik yazar ve ``changed`` yayınlar."""

    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...
    def update(self, values: Mapping[str, Any]) -> None: ...


@runtime_checkable
class EventBusAPI(Protocol):
    """``ctx.bus`` — Qt sinyali üstünde konu tabanlı yayın/abone. Ana iş parçacığına
    teslim edilir; başka iş parçacığından ``emit`` güvenlidir."""

    def emit(self, topic: str, payload: Any = None) -> None: ...
    def subscribe(self, topic: str, callback: Callable[[Any], None]) -> Callable[[], None]:
        """Abone ol; dönen fonksiyon aboneliği iptal eder."""


@runtime_checkable
class ProjectAPI(Protocol):
    """``ctx.project`` — açık proje oturumu (``storage/project.py``).

    Proje yokken ``is_open`` False'tur ve ``root`` ``None`` döner; modüller buna
    saygı göstermelidir (örn. indirici, proje yokken bile varsayılan kökü kullanır)."""

    is_open: bool

    @property
    def root(self) -> Optional[Path]: ...
    def subfolder(self, name: str) -> Path:
        """``PROJECT_SUBFOLDERS`` içinden bir klasörün mutlak yolu (yoksa oluşturur)."""
    def mark_dirty(self) -> None: ...


@runtime_checkable
class ContextAPI(Protocol):
    """Sayfa fabrikalarının ve servislerin gördüğü uygulama bağlamı (``ctx``).

    Somut sınıf: ``kadraj.core.context.AppContext``. Modüller bu arayüzün
    dışındaki şeye (örn. ``ctx.main_window``) dokunmamalıdır.
    """

    settings: SettingsAPI
    bus: EventBusAPI
    project: ProjectAPI
    jobs: JobQueueAPI
    vault: VaultAPI
    recent: RecentAPI
    undo: "QUndoStack"                           # geri alma geçmişi — proje durumuyla birlikte yaşar
    theme: Any                                   # kadraj.ui.theme.manager.ThemeManager (tokens, mode)
    sysmon: "SysmonAPI"                          # Adım 3 eklemesi
    providers: "ProviderRegistryAPI"             # Adım 3 eklemesi
    autosave: "AutosaveAPI"                      # Adım 3 eklemesi
    snapshots: "SnapshotAPI"                     # Adım 3 eklemesi

    @property
    def ui_mode(self) -> UiMode: ...
    def set_ui_mode(self, mode: "UiMode | str") -> None:
        """Modu değiştirir; yalnızca sayfaları/temayı değiştirir, durum korunur."""

    def service(self, module_id: str) -> Optional[Service]:
        """Modülün servisini (gerekirse üretip başlatarak) döner; yoksa ``None``."""

    def report_error(self, exc: BaseException,
                     context: Optional[ErrorContext] = None) -> SmartError:
        """İstisnayı ``translate`` ile çevirir, ``Topics.ERROR`` yayınlar, ``SmartError`` döner."""


# ===========================================================================
# 9. Adım 3 eklemeleri — YALNIZCA EKLEME; yukarıdaki hiçbir imza değişmedi
# ===========================================================================
@dataclass(frozen=True)
class VaultStatus:
    """Kasanın durumu. ``state``: ``empty`` (dosya yok) | ``ready`` (açık) | ``locked`` (parola bekliyor) |
    ``error`` (açılamıyor; ``error`` doludur)."""
    state: str
    mode: str = "none"                     # "keyring" | "passphrase" | "none"
    namespace: str = "default"
    error: Optional[SmartError] = None


@runtime_checkable
class VaultAdminAPI(Protocol):
    """Kasanın yönetim yüzü (``ctx.vault`` gerçek ``Vault`` olduğunda ``VaultAPI``'ye ek olarak vardır).

    * **Okuma yöntemleri** (``has_secret``/``masked``/``providers``) kasa kilitli ya da bozuksa istisna ATMAZ:
      ``False``/``""``/``[]`` döner; arayüz önce ``status()`` bakıp "kilitli"/hata kartı gösterir.
    * **Yazma ve düz okuma** (``set_secret``/``get_secret``/``delete_secret``) açılamayan kasada ``SmartError`` atar.
    * Profil ad alanı: ``use_namespace(ad)`` etkin ad alanını seçer (Haiku ``profiles.py`` adı üretir);
      ``view(ad)`` o ad alanına sabitlenmiş bir ``VaultAPI`` döner. Varsayılan ad alanı ``"default"``.
    * Anahtar yönetimi: OS keyring varsa kasa otomatik açılır (``mode="keyring"``); yoksa ya da başarısızsa
      parola türevli (PBKDF2-SHA256 + tuz) anahtar kullanılır (``mode="passphrase"``) ve her oturumda
      ``unlock(parola)`` gerekir.
    """

    namespace: str

    def status(self) -> VaultStatus: ...
    def needs_unlock(self) -> bool: ...
    def unlock(self, passphrase: str) -> None: ...
    def lock(self) -> None: ...
    def initialize(self, passphrase: Optional[str] = None) -> None: ...
    def change_passphrase(self, old: str, new: str) -> None: ...
    def use_namespace(self, namespace: str) -> None: ...
    def namespaces(self) -> List[str]: ...
    def view(self, namespace: str) -> VaultAPI: ...
    def delete_namespace(self, namespace: str) -> bool: ...
    def reset(self) -> None:
        """Mevcut kasa dosyasını ``.bad`` olarak yedekleyip boş kasayla başlar (kurtarılamayan durum)."""


@dataclass(frozen=True)
class Attempt:
    """``call_with_fallback`` içindeki tek bir sağlayıcı denemesi."""
    provider_id: str
    error: Optional[SmartError] = None
    skipped: bool = False                  # çevrimdışı/soğuma nedeniyle çağrılmadan atlandı
    retries: int = 0                       # 429 nedeniyle aynı sağlayıcıda yapılan yeniden deneme sayısı


@dataclass
class FallbackResult:
    ok: bool
    provider_id: str = ""
    value: Any = None
    attempts: List[Attempt] = field(default_factory=list)
    error: Optional[SmartError] = None     # ok=False ise son/özet hata
    queued_job: Optional[Job] = None       # hepsi başarısız olup ``queue_payload`` verildiyse kuyruğa alınan iş


@runtime_checkable
class ProviderRegistryAPI(Protocol):
    """``ctx.providers`` — sağlayıcı kayıt tablosu + fallback zinciri (bulut → yerel → kuyruğa al).

    ``test_connection`` Ayarlar'daki "Bağlantıyı Test Et" ışıklarını besler; asla istisna atmaz, çevrimdışıysa
    ``PingResult(ok=False, error=SmartError)`` döner. Sıra ``settings["fallback_order"][tür]`` ile
    verilebilir; yoksa kayıt sırasıyla bulutlar, sonra yerel olanlar. 429 alan sağlayıcı geri çekilmeye
    (exponential backoff, ``Retry-After`` saygılı) alınır.
    """

    def register(self, provider: Provider, *, replace: bool = False) -> None: ...
    def get(self, provider_id: str) -> Optional[Provider]: ...
    def list(self, kind: Optional[ProviderKind] = None) -> List[Provider]: ...
    def configured(self, vault: Optional[VaultAPI] = None, kind: Optional[ProviderKind] = None) -> List[Provider]: ...
    def test_connection(self, provider_id: str, vault: Optional[VaultAPI] = None,
                        timeout: float = 5.0) -> PingResult: ...
    def chain(self, kind: ProviderKind, vault: Optional[VaultAPI] = None) -> List[Provider]: ...
    def call_with_fallback(self, kind: ProviderKind, operation: Callable[[Provider], Any], *,
                           vault: Optional[VaultAPI] = None,
                           queue_payload: Optional[Mapping[str, Any]] = None,
                           queue_title: str = "") -> FallbackResult: ...


@runtime_checkable
class JobControlAPI(Protocol):
    """``ctx.jobs``'un ek denetimi (``JobQueueAPI``'ye ek). ``retry``: başarısız/iptal/duraklatılmış işi sıfırdan
    kuyruğa alır; ``resume``: duraklatılmış (örn. uygulama kapanırken yarım kalan) işi sürdürür."""

    def retry(self, job_id: int) -> bool: ...
    def resume(self, job_id: int) -> bool: ...


@dataclass(frozen=True)
class RingEntry:
    """Otomatik kayıt halkasındaki bir kayıt noktası."""
    time: float                            # epoch saniye
    path: str


@dataclass(frozen=True)
class RewindResult:
    """"10 Dk Önceye Döndür" sonucu. ``reason``: ``restored`` | ``no_change`` | ``no_history`` | ``no_project`` | ``error``."""
    ok: bool
    restored: bool
    reason: str
    minutes_requested: int = 10
    minutes_actual: float = 0.0            # gerçekte kaç dakika öncesine dönüldü (geçmiş yetmezse daha az)
    entry_time: Optional[float] = None
    error: Optional[SmartError] = None


@runtime_checkable
class AutosaveAPI(Protocol):
    """``ctx.autosave`` — 20 sn'de bir sessiz kayıt (yalnız değişiklik varsa) + dakikalık 10 dk geri dönüş halkası.

    Durum ``Topics.AUTOSAVE_STATE`` ile yayınlanır; ``Topics.REWIND_REQUESTED`` ``rewind``'e bağlıdır.
    Geri dönüş **geri alınabilir**: dönmeden önce mevcut durum yedeklenir, ``undo_rewind`` onu geri yükler."""

    state: str

    def tick(self) -> None: ...
    def ring(self) -> List[RingEntry]: ...
    def rewind(self, minutes: int = 10) -> RewindResult: ...
    def undo_rewind(self) -> RewindResult: ...


@dataclass(frozen=True)
class SnapshotInfo:
    id: int
    branch: str
    label: str
    created: float
    auto: bool = False
    parent_id: Optional[int] = None


@dataclass(frozen=True)
class SnapshotDiff:
    """İki snapshot arasındaki karşılaştırma özeti (``summary``: sade Türkçe satırlar, ``changes``: makine okur)."""
    a: int
    b: int
    summary: List[str] = field(default_factory=list)
    changes: Mapping[str, Any] = field(default_factory=dict)

    @property
    def identical(self) -> bool:
        return not self.summary


@runtime_checkable
class SnapshotAPI(Protocol):
    """``ctx.snapshots`` — Legend'in snapshot ağacı (ebeveynli, dallı). Proje yokken ``SmartError``."""

    def create(self, label: str = "", *, branch: Optional[str] = None, auto: bool = False) -> SnapshotInfo: ...
    def list(self, branch: Optional[str] = None) -> List[SnapshotInfo]: ...
    def branches(self) -> List[str]: ...
    def restore(self, snapshot_id: int) -> SnapshotInfo: ...
    def branch_from(self, snapshot_id: int, name: str) -> SnapshotInfo: ...
    def compare(self, a: int, b: int) -> SnapshotDiff: ...
    def delete(self, snapshot_id: int) -> bool: ...


@runtime_checkable
class SysmonAPI(Protocol):
    """``ctx.sysmon.sample(path=None)`` → ``{"cpu", "ram_used", "ram_total", "gpu", "vram_used", "vram_total",
    "gpu_name", "disk_free", "disk_total", "api_ping"}``; olmayan değer ``None`` ("—" gösterilir)."""

    def sample(self, path: Optional[str] = None) -> Dict[str, Any]: ...
    def record_ping(self, ms: Optional[float]) -> None: ...


# ===========================================================================
# 8. Registry modülleri nasıl keşfeder  (core/registry.py)
# ===========================================================================
MODULES_PACKAGE = "kadraj.modules"
MODULE_ENTRY = "module"          # kadraj/modules/<ad>/module.py
MODULE_ENTRY_FUNC = "register"   # def register() -> ModuleSpec

DISCOVERY_RULES = """
Keşif kuralları (kayıt defteri ``ModuleRegistry.discover()``):

1. ``kadraj/modules/`` altındaki her alt paket (``_`` ile başlayanlar hariç) bir modüldür.
2. Her modül ``kadraj/modules/<ad>/module.py`` dosyasında, **argümansız**
   ``def register() -> ModuleSpec`` işlevi sunar. ``spec.id`` paket adıyla aynı olmalıdır.
3. ``module.py`` içe aktarılırken ağır bağımlılık yüklememelidir (``yt_dlp``, ``sounddevice``
   vb. servis içinde, ilk kullanımda içe aktarılır). Sayfa/servis fabrikaları ``ModuleSpec``
   içinde *import-geç* (lazy) verilir:

       def register() -> ModuleSpec:
           def simple(ctx):
               from .page_simple import DownloaderSimplePage
               return DownloaderSimplePage(ctx)
           ...
           return ModuleSpec(id="downloader", title_key="downloader.title", order=10,
                             accent=Accent.PEACH, icon="download", simple_page=simple, ...)

4. ``spec.strings`` ({"tr": {...}, "en": {...}}) kayıt anında ``core.i18n.register_strings``
   ile birleştirilir; anahtarlar ``"<id>."`` ile başlar. Modül dizeleri ``strings.py``
   içinde yazılıp ``register()`` içinde ``strings=STRINGS`` olarak verilir.
5. Bir modülün içe aktarımı/``register()``'ı/``validate()``'i hata verirse modül *atlanır*,
   hata ``registry.errors`` listesine ``SmartError`` olarak yazılır; uygulama açılır ve
   o modül "yakında/yüklenemedi" yer tutucusuyla görünür. Bir modül asla çekirdeği düşürmez.
6. Planlı ama henüz yazılmamış modüller (downloader, recorder, editor, photo, series,
   settings) ``registry.PLANNED`` içinde bilinir; kayıtlı olmasalar da ana ekranda
   "yakında" kartıyla görünür.
7. Hata sözlüğü: ``kadraj/core/error_catalog.py`` içinde ``RULES: Sequence[ErrorRule]``.
   ``errors.translate`` bunu ilk çağrıda tembelce (lazy) yükler; dosya yoksa genel kartlara düşer.
8. Depolama kancaları: ``kadraj/storage/recent.py`` ``RecentAPI`` işlevlerini modül düzeyinde
   sunar (``get_recent``, ``add_recent``, ``forget``, ``clear``); ``kadraj/storage/folders.py``
   ``ensure_project_folders(project_dir: Path) -> list[Path]`` sunar. Çekirdek bunları
   ``try: import`` ile kullanır, yoksa yerleşik yedeğe düşer.
"""
