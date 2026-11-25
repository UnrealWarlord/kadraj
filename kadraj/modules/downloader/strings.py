"""Medya İndirici arayüz dizeleri (TR birincil, EN yedek).

Anahtarlar ``downloader.`` ile başlar (``ModuleSpec.validate``). Kodda anahtarlar
``K`` sabitleriyle kullanılır; böylece çekirdeğin i18n testi bu modülün anahtarlarını
çekirdek tabloda aramaz. Basit Mod metinlerinde teknik terim yoktur.
"""
from __future__ import annotations

from typing import Dict, Tuple

# ad → (Türkçe, English). Anahtar: "downloader." + ad (alt çizgi → nokta, küçük harf).
_PAIRS: Dict[str, Tuple[str, str]] = {
    "URL_INVALID_TITLE": ("Bağlantı tanınmadı", "Link not recognized"),
    "URL_INVALID_SUMMARY": ("Bağlantı http:// ya da https:// ile başlayan bir adres olmalı.",
                            "The link must start with http:// or https://."),
    "URL_INVALID_STEP": ("Video sayfasının adresini kopyalayıp buraya yapıştır.",
                         "Copy the address of the video page and paste it here."),
    "CHIP_IDLE": ("Bağlantı bekleniyor", "Waiting for a link"),
    "CHIP_SEARCHING": ("Bağlantı aranıyor…", "Looking up the link…"),
    "CHIP_FOUND": ("{site} videosu bulundu · {length}", "{site} video found · {length}"),
    "CHIP_PLAYLIST": ("Liste bulundu · {count} video", "Playlist found · {count} videos"),
    "BTN_PASTE": ("Yapıştır", "Paste"),
    "BTN_DOWNLOAD": ("İndir", "Download"),
    "BTN_QUEUE": ("Kuyruğa ekle", "Add to queue"),
    "BTN_SHOW_FOLDER": ("Klasörde Göster", "Show in folder"),
    "BTN_LIST_FORMATS": ("-F Listele", "-F List"),
    "BTN_CANCEL": ("İptal", "Cancel"),
    "MODE_AV_TITLE": ("Video + Ses", "Video + Audio"),
    "MODE_AV_SUB": ("Görüntü ve sesiyle birlikte", "With picture and sound"),
    "MODE_AUDIO_TITLE": ("Sadece Ses", "Audio only"),
    "MODE_AUDIO_SUB": (".wav olarak kaydedilir", "Saved as .wav"),
    "MODE_VIDEO_TITLE": ("Sadece Görüntü", "Picture only"),
    "MODE_VIDEO_SUB": ("Sessiz video", "Silent video"),
    "PARTIAL_TOGGLE": ("Sadece bir kısmı", "Only a part of it"),
    "RANGE_SUMMARY": ("{start}'den başla · {length} seçtin · {end}'te bitir",
                      "From {start} · {length} selected · ends at {end}"),
    "RANGE_WHOLE": ("Tüm video", "Whole video"),
    "RANGE_INVALID": ("Bitiş, başlangıçtan en az 1 saniye sonra olmalı.",
                      "The end must be at least 1 second after the start."),
    "QUALITY_FAST": ("Hızlı", "Fast"),
    "QUALITY_FAST_SUB": ("720p", "720p"),
    "QUALITY_BALANCED": ("Dengeli", "Balanced"),
    "QUALITY_BALANCED_SUB": ("1080p", "1080p"),
    "QUALITY_BEST": ("En Yüksek", "Highest"),
    "QUALITY_BEST_SUB": ("4K", "4K"),
    "SIZE_ABOUT": ("~{size}", "~{size}"),
    "SIZE_UNKNOWN": ("boyut bilinmiyor", "size unknown"),
    "RECENT_TITLE": ("Son indirilenler", "Recent downloads"),
    "RECENT_EMPTY": ("Henüz indirme yok.", "No downloads yet."),
    "TARGET": ("Kaydedilecek yer: {folder}", "Saved to: {folder}"),
    "PROGRESS_DOWNLOADING": ("İndiriliyor · {speed}", "Downloading · {speed}"),
    "PROGRESS_PROCESSING": ("Dönüştürülüyor…", "Converting…"),
    "STATUS_QUEUED": ("Sırada", "Queued"),
    "STATUS_RUNNING": ("İndiriliyor", "Downloading"),
    "STATUS_DONE": ("Bitti", "Done"),
    "STATUS_FAILED": ("Olmadı", "Failed"),
    "STATUS_CANCELLED": ("İptal edildi", "Cancelled"),
    "STATUS_PAUSED": ("Duraklatıldı", "Paused"),
    "NEED_LINK": ("Önce bir bağlantı yapıştır.", "Paste a link first."),
    "QUEUED_ONE": ("Kuyruğa eklendi: {title}", "Added to the queue: {title}"),
    "QUEUED_MANY": ("{count} video kuyruğa eklendi.", "{count} videos added to the queue."),
    "OPT_SUBS": ("Altyazı (.srt)", "Subtitles (.srt)"),
    "OPT_THUMB": ("Kapak resmi", "Thumbnail"),
    "OPT_EMBED": ("Kapağı dosyaya göm", "Embed thumbnail"),
    "OPT_PLAYLIST": ("Listenin tamamı", "Whole playlist"),
    "OPT_RATE": ("Hız sınırı 5 MB/s", "Speed limit 5 MB/s"),
    "QUEUE_TITLE": ("Başlık", "Title"),
    "QUEUE_STATUS": ("Durum", "Status"),
    "QUEUE_PROGRESS": ("İlerleme", "Progress"),
    "META": ("{site} · {length} · en yüksek {height}p · altyazı: {subs}",
             "{site} · {length} · max {height}p · subtitles: {subs}"),
    "NO_SUBS": ("yok", "none"),
    "FORMAT_HEADER_HINT": ("Video ve ses biçimlerini Ctrl tuşuyla birlikte seçebilirsin.",
                           "Hold Ctrl to select a video and an audio format together."),
    "URL_PLACEHOLDER": ("Video bağlantısını buraya yapıştır", "Paste the video link here"),
    "LEGEND_MODE": ("Biçim", "Format"),
    "LEGEND_QUALITY": ("En büyük çözünürlük", "Largest resolution"),
    "LEGEND_RANGE": ("Aralık (IN / OUT)", "Range (IN / OUT)"),
    "LEGEND_OPTIONS": ("Seçenekler", "Options"),
    "LEGEND_COMMAND": ("Komut önizlemesi", "Command preview"),
    "LEGEND_FORMATS": ("Biçimler", "Formats"),
    "LEGEND_QUEUE": ("İndirme kuyruğu", "Download queue"),
    "LEGEND_SUB_LANGS": ("Altyazı dilleri", "Subtitle languages"),
    "TIMECODE_INVALID": ("Zaman kodu 00:00:00 biçiminde olmalı.", "Timecode must look like 00:00:00."),
    "PLAYLIST_NOTE": ("Liste: her video ayrı iş olarak kuyruğa girer.",
                      "Playlist: each video is queued as a separate job."),
}


class K:
    """Anahtar sabitleri: ``K.URL_INVALID_TITLE == "downloader.url.invalid.title"``."""


STRINGS: Dict[str, Dict[str, str]] = {"tr": {}, "en": {}}
for _name, (_tr, _en) in _PAIRS.items():
    _key = "downloader." + _name.lower().replace("_", ".")
    setattr(K, _name, _key)
    STRINGS["tr"][_key] = _tr
    STRINGS["en"][_key] = _en
