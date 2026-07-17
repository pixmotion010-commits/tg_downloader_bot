import os
import glob
import uuid
import asyncio
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import yt_dlp

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Tokenni shu yerga yozing
TOKEN = "8895598746:AAGcsyp2iq7bvzDbXyOzz1ibq4fazks-kjc"


def get_cookie_file():
    cookie_files = glob.glob("*cookie*.txt")
    return cookie_files[0] if cookie_files else None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🖐 Salom!\n"
        "▶️ YouTube va Instagram'dan video/audio yuklovchi bot.\n"
        "🔗 Link yuboring."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    is_youtube = "youtube.com" in url or "youtu.be" in url
    is_instagram = "instagram.com" in url

    if not (is_youtube or is_instagram):
        await update.message.reply_text("⚠️ Faqat YouTube yoki Instagram havola yuboring.")
        return

    # Linkni vaqtincha context.user_data ga saqlaymiz (callback ichida ishlatish uchun)
    request_id = uuid.uuid4().hex[:8]
    context.user_data[request_id] = url

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎥 Video", callback_data=f"video|{request_id}"),
            InlineKeyboardButton("🎵 Audio", callback_data=f"audio|{request_id}"),
        ]
    ])
    await update.message.reply_text("❓ Nimani yuklab beray?", reply_markup=keyboard)


async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        media_type, request_id = query.data.split("|", 1)
    except ValueError:
        await query.edit_message_text("❌ Xatolik: noto'g'ri so'rov.")
        return

    url = context.user_data.get(request_id)
    if not url:
        await query.edit_message_text("⌛️ Link muddati o'tgan, iltimos linkni qayta yuboring.")
        return

    emoji = "🎥" if media_type == "video" else "🎵"
    label = "Video" if media_type == "video" else "Audio"
    await query.edit_message_text(f"📥 {emoji} {label} yuklanmoqda, kuting...")

    chat_id = query.message.chat_id
    file_id = uuid.uuid4().hex[:8]
    base_name = f"media_{chat_id}_{file_id}"

    cookie_file = get_cookie_file()

    if media_type == "video":
        output_template = base_name + ".%(ext)s"
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'merge_output_format': 'mp4',
            'outtmpl': output_template,
            'quiet': True,
            'noplaylist': True,
            'no_warnings': True,
        }
    else:  # audio
        output_template = base_name + ".%(ext)s"
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'quiet': True,
            'noplaylist': True,
            'no_warnings': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }

    if cookie_file:
        ydl_opts['cookiefile'] = cookie_file

    downloaded_file = None
    try:
        def do_download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info), info

        # Yuklashni alohida threadda bajarish - botni bloklamaslik uchun
        filename, info = await asyncio.to_thread(do_download)

        # Audio uchun postprocessor kengaytmani .mp3 ga o'zgartiradi
        if media_type == "audio":
            filename = os.path.splitext(filename)[0] + ".mp3"

        downloaded_file = filename

        if not os.path.exists(downloaded_file):
            raise FileNotFoundError("Yuklangan fayl topilmadi (format/ffmpeg muammosi bo'lishi mumkin).")

        # Telegram caption limiti - 1024 belgi. Uzun bo'lsa kesib,
        # to'liq matnni alohida xabar sifatida yuboramiz (hech narsa yo'qolmasin).
        full_caption = info.get('description') or info.get('title') or ""
        if len(full_caption) > 1000:
            short_caption = full_caption[:1000] + "..."
        else:
            short_caption = full_caption

        await query.edit_message_text(f"📤 {emoji} Telegram'ga yuborilmoqda...")

        with open(downloaded_file, 'rb') as media_file:
            if media_type == "video":
                await context.bot.send_video(
                    chat_id=chat_id, video=media_file, caption=short_caption
                )
            else:
                await context.bot.send_audio(
                    chat_id=chat_id, audio=media_file, caption=short_caption
                )

        # Agar caption 1000 belgidan uzun bo'lsa, to'liq matnni alohida yuboramiz
        if len(full_caption) > 1000:
            await context.bot.send_message(
                chat_id=chat_id, text=f"📝 To'liq matn:\n\n{full_caption}"
            )

        await query.delete_message()

    except Exception as e:
        logger.error(f"Xatolik: {e}")
        await query.edit_message_text(f"❌ Xatolik: {str(e)[:200]}")
    finally:
        if downloaded_file and os.path.exists(downloaded_file):
            os.remove(downloaded_file)
        context.user_data.pop(request_id, None)


if __name__ == '__main__':
    if TOKEN == "BU_YERGA_BOTFATHERDAN_OLGAN_TOKENINGIZNI_YOZING":
        print("❌ XATOLIK: Tokenni kiriting!")
    else:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        app.add_handler(CallbackQueryHandler(handle_choice))
        app.run_polling()
