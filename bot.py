"""
YouTube / Instagram Video-Audio Yuklovchi Telegram Bot
========================================================
Foydalanuvchi YouTube yoki Instagram havolasini yuborsa, bot video yoki
audio (mp3) formatida yuklab, Telegram orqali qaytarib beradi.

Talablar:
    pip install python-telegram-bot yt-dlp
    ffmpeg o'rnatilgan bo'lishi shart (video birlashtirish va audio ajratish uchun)

Ishga tushirish:
    Tokenni quyida BOT_TOKEN o'zgaruvchisiga yozing (yoki TELEGRAM_BOT_TOKEN
    muhit o'zgaruvchisi orqali bering), so'ng:
        python bot.py
"""

import os
import re
import glob
import uuid
import time
import shutil
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError, RetryAfter, TimedOut
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import yt_dlp


# ─────────────────────────────────────────────────────────────────────────
#  SOZLAMALAR
# ─────────────────────────────────────────────────────────────────────────

# Token: avval muhit o'zgaruvchisidan, bo'lmasa quyidagi qatordan olinadi.
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8895598746:AAGcsyp2iq7bvzDbXyOzz1ibq4fazks-kjc")

# Vaqtinchalik fayllar shu papkaga yoziladi (bot ishlagan joyda avtomatik yaratiladi)
TEMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads_tmp")

# Telegram oddiy Bot API orqali yuborish mumkin bo'lgan MAKSIMAL fayl hajmi (baytda)
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

# Bir foydalanuvchi ketma-ket nechta so'rovni bir vaqtda yuborishi mumkinligi
MAX_CONCURRENT_PER_USER = 1

# Bir xil foydalanuvchi ikkita so'rov orasida kutishi kerak bo'lgan minimal vaqt (soniya)
MIN_SECONDS_BETWEEN_REQUESTS = 3

# Caption uzunligi (Telegram limiti 1024, xavfsizlik uchun ozroq olamiz)
CAPTION_SOFT_LIMIT = 1000

LOG_LEVEL = logging.INFO

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=LOG_LEVEL,
)
# python-telegram-bot va yt-dlp'ning ortiqcha "shovqinli" loglarini kamaytiramiz
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger("media_bot")


YOUTUBE_URL_RE = re.compile(r"(youtube\.com|youtu\.be)", re.IGNORECASE)
INSTAGRAM_URL_RE = re.compile(r"instagram\.com", re.IGNORECASE)

COMMON_YDL_OPTS = {
    "quiet": True,
    "noprogress": True,
    "no_warnings": True,
    "noplaylist": True,
    "retries": 10,
    "fragment_retries": 10,
    "extractor_retries": 5,
    "socket_timeout": 30,
    "nocheckcertificate": True,
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        )
    },
    # YouTube uchun android/web klient formatlari ko'proq mos formatlarni beradi
    "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
}

VIDEO_FORMAT_CANDIDATES = [
    "bestvideo[ext=mp4][filesize<50M]+bestaudio[ext=m4a]/best[ext=mp4][filesize<50M]",
    "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
    "bestvideo+bestaudio/best",
    "best",
]
AUDIO_FORMAT_CANDIDATES = [
    "bestaudio[ext=m4a][filesize<50M]/bestaudio[filesize<50M]",
    "bestaudio[ext=m4a]/bestaudio",
    "best",
]


# ─────────────────────────────────────────────────────────────────────────
#  FOYDALANUVCHI HOLATINI KUZATISH (oddiy in-memory rate limit)
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class UserState:
    busy: bool = False
    last_request_ts: float = field(default_factory=lambda: 0.0)


user_states: dict[int, UserState] = {}


def get_user_state(user_id: int) -> UserState:
    if user_id not in user_states:
        user_states[user_id] = UserState()
    return user_states[user_id]


# ─────────────────────────────────────────────────────────────────────────
#  YORDAMCHI FUNKSIYALAR
# ─────────────────────────────────────────────────────────────────────────

def ensure_temp_dir() -> None:
    os.makedirs(TEMP_DIR, exist_ok=True)


def cleanup_old_temp_files(max_age_seconds: int = 3600) -> None:
    """Bot qayta ishga tushganda yoki vaqti-vaqti bilan eskirgan vaqtinchalik
    fayllarni tozalaydi (masalan, bot kutilmaganda o'chib qolgan holatlar)."""
    ensure_temp_dir()
    now = time.time()
    for path in glob.glob(os.path.join(TEMP_DIR, "*")):
        try:
            if now - os.path.getmtime(path) > max_age_seconds:
                os.remove(path)
        except OSError:
            pass


def detect_platform(url: str) -> Optional[str]:
    if YOUTUBE_URL_RE.search(url):
        return "youtube"
    if INSTAGRAM_URL_RE.search(url):
        return "instagram"
    return None


def extract_first_url(text: str) -> Optional[str]:
    """Xabar ichidan birinchi http(s) havolani ajratib oladi
    (foydalanuvchi qo'shimcha matn bilan link yuborgan holatlar uchun ham)."""
    match = re.search(r"https?://\S+", text)
    return match.group(0) if match else None


def get_cookie_file(platform: str) -> Optional[str]:
    """Platformaga mos cookie faylni topadi.
    Masalan: youtube_cookies.txt, instagram_cookies.txt yoki umumiy cookies.txt"""
    candidates = glob.glob(f"*{platform}*cookie*.txt") or glob.glob("*cookie*.txt")
    return candidates[0] if candidates else None


def human_size(num_bytes: Optional[float]) -> str:
    if not num_bytes:
        return "noma'lum"
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def safe_delete(path: Optional[str]) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError as e:
            logger.warning(f"Faylni o'chirishda muammo ({path}): {e}")


def build_ydl_opts(media_type: str, output_template: str, fmt: str,
                    cookie_file: Optional[str], progress_hook) -> dict:
    opts = dict(COMMON_YDL_OPTS)
    opts["outtmpl"] = output_template
    opts["format"] = fmt
    opts["progress_hooks"] = [progress_hook]

    if media_type == "video":
        opts["merge_output_format"] = "mp4"
    else:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    if cookie_file:
        opts["cookiefile"] = cookie_file

    return opts


async def safe_edit(message, text: str) -> None:
    """Xabarni tahrirlashda yuzaga keladigan kichik Telegram xatolarini
    (masalan 'message not modified' yoki flood limit) yutib yuboradi,
    shunda foydalanuvchiga hech qanday texnik xato ko'rinmaydi."""
    try:
        await message.edit_text(text)
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after)
        try:
            await message.edit_text(text)
        except TelegramError:
            pass
    except TelegramError:
        pass


# ─────────────────────────────────────────────────────────────────────────
#  BUYRUQLAR
# ─────────────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🖐 Salom!\n"
        "▶️ Men YouTube va Instagram'dan video yoki audio (mp3) yuklab beraman.\n"
        "🔗 Menga shunchaki havolani yuboring.\n\n"
        "ℹ️ Qo'shimcha buyruqlar uchun /help yozing."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 Yordam\n\n"
        "1️⃣ YouTube yoki Instagram havolasini yuboring\n"
        "2️⃣ 🎥 Video yoki 🎵 Audio tugmasini tanlang\n"
        "3️⃣ Fayl tayyor bo'lgach avtomatik yuboriladi\n\n"
        "⚠️ Eslatma: Telegram orqali 50MB dan katta fayllarni yuborib bo'lmaydi."
    )


# ─────────────────────────────────────────────────────────────────────────
#  LINK QABUL QILISH
# ─────────────────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    state = get_user_state(user_id)

    raw_text = update.message.text.strip()
    url = extract_first_url(raw_text) or raw_text
    platform = detect_platform(url)

    if not platform:
        await update.message.reply_text(
            "⚠️ Bu havola tanilmadi.\n"
            "📌 Faqat YouTube yoki Instagram havolasini yuboring."
        )
        return

    # Spam / bir vaqtda ko'p so'rov yuborishning oldini olish
    if state.busy:
        await update.message.reply_text(
            "⏳ Avvalgi so'rovingiz hali tugallanmadi. Iltimos kuting."
        )
        return

    now = time.time()
    if now - state.last_request_ts < MIN_SECONDS_BETWEEN_REQUESTS:
        await update.message.reply_text(
            "🐢 Juda tez-tez so'rov yubordingiz. Bir necha soniya kutib qayta urinib ko'ring."
        )
        return
    state.last_request_ts = now

    request_id = uuid.uuid4().hex[:10]
    context.user_data[request_id] = {"url": url, "platform": platform}

    platform_emoji = "▶️" if platform == "youtube" else "📸"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎥 Video", callback_data=f"dl|video|{request_id}"),
            InlineKeyboardButton("🎵 Audio", callback_data=f"dl|audio|{request_id}"),
        ]
    ])
    await update.message.reply_text(
        f"{platform_emoji} Havola qabul qilindi.\n❓ Nimani yuklab beray?",
        reply_markup=keyboard,
    )


# ─────────────────────────────────────────────────────────────────────────
#  YUKLASH JARAYONI (progress bilan)
# ─────────────────────────────────────────────────────────────────────────

def _blocking_download(url: str, media_type: str, output_template: str,
                        cookie_file: Optional[str], formats: list[str],
                        progress_hook) -> tuple[str, dict]:
    """Sinxron (blocking) yuklash funksiyasi — asyncio.to_thread orqali chaqiriladi.
    Formatlar ro'yxatini birma-bir sinab, birinchi muvaffaqiyatlisini qaytaradi."""
    last_error: Optional[Exception] = None

    for fmt in formats:
        opts = build_ydl_opts(media_type, output_template, fmt, cookie_file, progress_hook)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                return filename, info
        except Exception as e:  # noqa: BLE001 - qasddan keng tutamiz, keyingi formatga o'tish uchun
            last_error = e
            logger.warning(f"Format muvaffaqiyatsiz [{fmt}]: {e}")
            continue

    raise last_error or RuntimeError("Hech qanday format ishlamadi")


async def handle_download_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    state = get_user_state(user_id)

    parts = (query.data or "").split("|", 2)
    if len(parts) != 3 or parts[0] != "dl":
        await safe_edit(query.message, "🔄 Noto'g'ri so'rov, iltimos linkni qayta yuboring.")
        return

    _, media_type, request_id = parts
    payload = context.user_data.get(request_id)

    if not payload:
        await safe_edit(query.message, "🔄 Havola muddati o'tgan. Iltimos, linkni qayta yuboring.")
        return

    url = payload["url"]
    emoji = "🎥" if media_type == "video" else "🎵"
    label = "Video" if media_type == "video" else "Audio"
    chat_id = query.message.chat_id

    state.busy = True
    status_msg = query.message
    await safe_edit(status_msg, f"📥 {emoji} {label} yuklanmoqda... 0%")

    ensure_temp_dir()
    file_id = uuid.uuid4().hex[:10]
    base_name = os.path.join(TEMP_DIR, f"media_{chat_id}_{file_id}")
    output_template = base_name + ".%(ext)s"

    cookie_file = get_cookie_file(payload["platform"])
    formats = VIDEO_FORMAT_CANDIDATES if media_type == "video" else AUDIO_FORMAT_CANDIDATES

    progress = {"percent": -1, "sent": -1}

    def progress_hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            if total:
                progress["percent"] = min(99, int(downloaded / total * 100))
        elif d.get("status") == "finished":
            progress["percent"] = 100

    stop_event = asyncio.Event()

    async def progress_updater() -> None:
        while not stop_event.is_set():
            p = progress["percent"]
            if p >= 0 and p != progress["sent"] and (p - progress["sent"] >= 7 or p == 100):
                progress["sent"] = p
                await safe_edit(status_msg, f"📥 {emoji} {label} yuklanmoqda... {p}%")
            await asyncio.sleep(1.5)

    updater_task = asyncio.create_task(progress_updater())

    downloaded_file: Optional[str] = None
    success = False

    try:
        try:
            filename, info = await asyncio.to_thread(
                _blocking_download, url, media_type, output_template,
                cookie_file, formats, progress_hook,
            )
        except Exception as e:
            logger.error(f"[{request_id}] Yuklab olishda yakuniy xatolik: {e}")
            filename, info = None, None

        stop_event.set()
        await updater_task

        if not filename:
            await safe_edit(
                status_msg,
                "😕 Afsuski, hozircha bu havolani yuklab bo'lmadi.\n"
                "🔁 Birozdan so'ng qaytadan urinib ko'ring yoki boshqa havola yuboring."
            )
            return

        if media_type == "audio":
            filename = os.path.splitext(filename)[0] + ".mp3"

        downloaded_file = filename

        if not os.path.exists(downloaded_file):
            logger.error(f"[{request_id}] Yuklangan fayl topilmadi: {downloaded_file}")
            await safe_edit(
                status_msg,
                "😕 Fayl tayyorlashda muammo yuz berdi.\n"
                "🔁 Iltimos, qaytadan urinib ko'ring."
            )
            return

        file_size = os.path.getsize(downloaded_file)
        if file_size > MAX_FILE_SIZE_BYTES:
            await safe_edit(
                status_msg,
                f"📦 Fayl juda katta ({human_size(file_size)}).\n"
                "⚠️ Telegram 50MB dan katta fayllarni yuborishga ruxsat bermaydi.\n"
                "🔁 Boshqa sifat yoki audio formatini tanlab ko'ring."
            )
            return

        caption_source = (info.get("description") or info.get("title") or "") if info else ""
        if len(caption_source) > CAPTION_SOFT_LIMIT:
            short_caption = caption_source[:CAPTION_SOFT_LIMIT] + "..."
        else:
            short_caption = caption_source

        await safe_edit(status_msg, f"📤 {emoji} Telegram'ga yuborilmoqda...")

        with open(downloaded_file, "rb") as media_file:
            if media_type == "video":
                await context.bot.send_video(
                    chat_id=chat_id, video=media_file, caption=short_caption,
                    supports_streaming=True, read_timeout=120, write_timeout=120,
                )
            else:
                await context.bot.send_audio(
                    chat_id=chat_id, audio=media_file, caption=short_caption,
                    read_timeout=120, write_timeout=120,
                )

        if len(caption_source) > CAPTION_SOFT_LIMIT:
            await context.bot.send_message(
                chat_id=chat_id, text=f"📝 To'liq matn:\n\n{caption_source}"
            )

        try:
            await status_msg.delete()
        except TelegramError:
            pass

        success = True

    except (TimedOut, RetryAfter) as e:
        logger.error(f"[{request_id}] Telegram tarmoq xatosi: {e}")
        await safe_edit(
            status_msg,
            "🌐 Tarmoq bilan bog'lanishda muammo bo'ldi.\n"
            "🔁 Iltimos, birozdan so'ng qaytadan urinib ko'ring."
        )
    except Exception as e:  # noqa: BLE001 - foydalanuvchiga texnik detal chiqmasligi kerak
        logger.error(f"[{request_id}] Kutilmagan xatolik: {e}")
        await safe_edit(
            status_msg,
            "😕 Nimadir xato ketdi.\n"
            "🔁 Iltimos, birozdan so'ng qaytadan urinib ko'ring."
        )
    finally:
        if not stop_event.is_set():
            stop_event.set()
        safe_delete(downloaded_file)
        context.user_data.pop(request_id, None)
        state.busy = False
        logger.info(f"[{request_id}] Yakunlandi: {'muvaffaqiyatli' if success else 'muvaffaqiyatsiz'}")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global xato ushlagich — bot butunlay yiqilib qolmasligi uchun."""
    logger.error(f"Kutilmagan xatolik: {context.error}", exc_info=context.error)


# ─────────────────────────────────────────────────────────────────────────
#  ISHGA TUSHIRISH
# ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "BU_YERGA_BOTFATHERDAN_OLGAN_TOKENINGIZNI_YOZING":
        print("❌ XATOLIK: Avval bot tokenini kiriting (BOT_TOKEN yoki TELEGRAM_BOT_TOKEN).")
        return

    if shutil.which("ffmpeg") is None:
        logger.warning(
            "⚠️  ffmpeg topilmadi! Video birlashtirish va audio ajratish ishlamasligi mumkin. "
            "O'rnatish: sudo apt install ffmpeg"
        )

    cleanup_old_temp_files()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(CallbackQueryHandler(handle_download_choice, pattern=r"^dl\|"))
    app.add_error_handler(on_error)

    logger.info("🤖 Bot ishga tushdi...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
