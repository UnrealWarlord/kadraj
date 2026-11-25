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
    "BTN_PLAY": ("Oynat", "Play"),
    "BTN_SEND_EDITOR": ("Düzenlemeye gönder", "Send to editor"),
    "EDITOR_SENT": ("Düzenleyiciye eklendi: {name}", "Added to the editor: {name}"),
    "EDITOR_UNAVAILABLE": ("Düzenleyici şu an kullanılamıyor.", "The editor is not available right now."),
    "EDITOR_FAILED": ("Dosya düzenleyiciye eklenemedi. Dosyanın bozulmadığından emin ol ve yeniden dene.",
                      "The file could not be added to the editor. Make sure it is intact and try again."),
    "PLAY_FAILED": ("Oynatıcı açılamadı. “Klasörde Göster” ile dosyayı bulup açabilirsin.",
                    "The player could not be opened. Use “Show in folder” to find and open the file."),
    "FOLDER_FAILED": ("Klasör açılamadı. Dosyayı kendin bulmak için Ayarlar'daki indirme klasörüne bak.",
                      "The folder could not be opened. Look in the download folder from Settings."),
    "FAILED_TITLE": ("İndirme tamamlanamadı", "The download did not finish"),
    "FAILED_CAUSE": ("Bağlantı, sitenin kendisi ya da bu video bu indiriciyle uyumsuz olabilir.",
                     "The link, the site, or this video may not work with the downloader."),
    "FAILED_STEP1": ("Bağlantıyı tarayıcıdan yeniden kopyalayıp burada tekrar dene.",
                     "Copy the link again from your browser and retry here."),
    "FAILED_STEP2": ("Kaliteyi ya da biçimi bir kademe değiştirip dene.",
                     "Change the quality or format one step and retry."),
    "FAILED_STEP3": ("Yine olmazsa birkaç saat sonra tekrar dene; site değişmiş olabilir.",
                     "If it still fails, try again in a few hours; the site may have changed."),
    "UNKNOWN_ERROR": ("Bilinmeyen bir sorun oldu.", "An unknown problem happened."),
    "QUALITY_UNAVAILABLE": ("Bu videoda yok (en yüksek: {height}p)", "Not in this video (highest: {height}p)"),
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


# Hata kartları (Akıllı Hata): kimlik → (TR, EN); her biri (başlık, özet, neden, (adım1, adım2, adım3)).
# Anahtarlar: ERR_<KİMLİK>_TITLE / _SUMMARY / _CAUSE / _STEP1..3. Adımlar numaralı, sade ve sırayla yapılacak şeyler.
_ERR: Dict[str, Tuple[Tuple[str, str, str, Tuple[str, ...]], Tuple[str, str, str, Tuple[str, ...]]]] = {
    "JS": (
        ("Video çözücü bileşen eksik ya da eski",
         "Bu videonun kaliteleri okunamadı, bu yüzden indirme olmadı.",
         "YouTube artık videoyu açmak için küçük bir yardımcı program (Deno) istiyor; bilgisayarında yok ya da eski kalmış.",
         ("Aşağıdaki “Bileşenleri şimdi güncelle” düğmesine bas ve bitmesini bekle.",
          "Bağlantıyı yeniden yapıştırıp indirmeyi dene.",
          "Yine olmazsa Ayarlar → Araçlar bölümünde bileşenlerin yanında ✓ işareti olup olmadığına bak.")),
        ("Video decoder component is missing or outdated",
         "The qualities of this video could not be read, so nothing was downloaded.",
         "YouTube now needs a small helper program (Deno) to open the video; it is missing or too old on this computer.",
         ("Press “Update components now” below and wait for it to finish.",
          "Paste the link again and retry the download.",
          "If it still fails, open Settings → Tools and check that the components show a ✓.")),
    ),
    "FFMPEG": (
        ("Ses ve görüntü birleştirilemedi",
         "FFmpeg bulunamadığı için dosya hazırlanamadı.",
         "Uygulamanın ses-görüntü aracı (FFmpeg) bilgisayarda bulunamadı.",
         ("Aşağıdaki “Bileşenleri şimdi güncelle” düğmesine bas; FFmpeg kendiliğinden indirilir.",
          "İndirme bitince aynı bağlantıyı yeniden dene.",
          "İnternet yoksa bağlandıktan sonra tekrar dene; Ayarlar → Araçlar bölümü durumu gösterir.")),
        ("Audio and video could not be merged",
         "The file could not be prepared because FFmpeg was not found.",
         "The app's audio/video tool (FFmpeg) was not found on this computer.",
         ("Press “Update components now” below; FFmpeg is downloaded automatically.",
          "When it finishes, retry the same link.",
          "If you are offline, retry once connected; Settings → Tools shows the status.")),
    ),
    "OUTDATED": (
        ("Video indirici eski kalmış",
         "Bu site videoyu değiştirmiş; eski sürüm artık okuyamıyor.",
         "Siteler sık sık değişir, indirici de buna göre güncellenir. Senin sürümün eski kalmış.",
         ("Aşağıdaki “Bileşenleri şimdi güncelle” düğmesine bas.",
          "Güncelleme bitince bağlantıyı yeniden dene.",
          "Yine olmazsa birkaç saat sonra tekrar dene; yeni sürüm henüz çıkmamış olabilir.")),
        ("The downloader is out of date",
         "The site changed and the old version can no longer read the video.",
         "Sites change often and the downloader is updated to follow them. Your version is behind.",
         ("Press “Update components now” below.",
          "When it finishes, retry the link.",
          "If it still fails, try again in a few hours; the new version may not be out yet.")),
    ),
    "FORMAT": (
        ("İstenen kalite bu videoda yok",
         "Seçtiğin kalitede bir dosya bulunamadı.",
         "Video o çözünürlükte yayınlanmamış olabilir.",
         ("Kaliteyi bir kademe düşür (örneğin 1080p ya da 720p).",
          "Yine olmazsa “Bileşenleri şimdi güncelle” düğmesine bas.",
          "Sonra yeniden dene.")),
        ("The requested quality is not available",
         "No file exists in the quality you chose.",
         "The video may not be published at that resolution.",
         ("Pick a lower quality (for example 1080p or 720p).",
          "If it still fails, press “Update components now”.",
          "Then retry.")),
    ),
    "BOT": (
        ("YouTube şu an doğrulama istiyor",
         "YouTube bu bağlantıdan gelen isteği “robot olabilir” diye durdurdu.",
         "Bu, YouTube'un geçici bir koruması; internet bağlantın ya da çok sayıda istek yüzünden çıkabilir.",
         ("Birkaç dakika bekleyip yeniden dene.",
          "“Bileşenleri şimdi güncelle” düğmesine basıp yeniden dene.",
          "Olmazsa başka bir internet bağlantısıyla (örneğin telefon hotspot'u) dene.")),
        ("YouTube is asking for verification",
         "YouTube stopped this request, suspecting it could be a bot.",
         "This is a temporary YouTube protection, often triggered by the network or many requests.",
         ("Wait a few minutes and retry.",
          "Press “Update components now” and retry.",
          "If it persists, try another network (for example a phone hotspot).")),
    ),
}
_PAIRS["ERR_ACTION_UPDATE"] = ("Bileşenleri şimdi güncelle", "Update components now")
_PAIRS["ERR_ACTION_TOOLS"] = ("Araçlar bölümünü aç", "Open the Tools section")
for _id, (_tr_e, _en_e) in _ERR.items():
    for _i, _field in enumerate(("TITLE", "SUMMARY", "CAUSE")):
        _PAIRS[f"ERR_{_id}_{_field}"] = (_tr_e[_i], _en_e[_i])
    for _n in range(3):
        _PAIRS[f"ERR_{_id}_STEP{_n + 1}"] = (_tr_e[3][_n], _en_e[3][_n])
ERR_IDS: Tuple[str, ...] = tuple(_ERR)


class K:
    """Anahtar sabitleri: ``K.URL_INVALID_TITLE == "downloader.url.invalid.title"``."""


STRINGS: Dict[str, Dict[str, str]] = {"tr": {}, "en": {}}
for _name, (_tr, _en) in _PAIRS.items():
    _key = "downloader." + _name.lower().replace("_", ".")
    setattr(K, _name, _key)
    STRINGS["tr"][_key] = _tr
    STRINGS["en"][_key] = _en
