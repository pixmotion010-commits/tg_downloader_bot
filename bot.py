import os
import time
import asyncio
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

TOKEN = "8965637635:AAFmjmgvDqxIqBxt6UeChdKxTnivGNilVgo "  # <-- Bu yerga o'zingizning asl tokeningizni yozing

# Foydalanuvchi yuborgan oxirgi havolani vaqtincha saqlab turish uchun
user_links = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Menga YouTube yoki Instagram havolasini yuboring — "
        "video yoki audio (mp3) yuklab beraman 🎬🎵"
    )


async def link_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not url.startswith("http"):
        await update.message.reply_text("Iltimos, to'g'ri havola yuboring.")
        return

    # Havolani foydalanuvchi ID'siga bog'lab saqlaymiz
    user_links[update.effective_user.id] = url

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 Video", callback_data="video"),
            InlineKeyboardButton("🎵 Audio", callback_data="audio"),
        ]
    ])

    await update.message.reply_text(
        "Qaysi formatda yuklab beray?",
        reply_markup=keyboard
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    choice = query.data  # "video" yoki "audio"
    user_id = query.from_user.id
    url = user_links.get(user_id)

    if not url:
        await query.edit_message_text("Havola topilmadi, iltimos qaytadan yuboring.")
        return

    label = "Video" if choice == "video" else "Audio"
    await query.edit_message_text(f"⏳ {label} yuklanmoqda... 0%")

    os.makedirs("downloads", exist_ok=True)

    loop = asyncio.get_running_loop()
    # Xabarni juda tez-tez yangilanishining oldini olish uchun oxirgi holatni saqlaymiz
    progress_state = {"last_percent": -1, "last_edit_time": 0.0}

    def progress_hook(d):
        if d.get("status") != "downloading":
            return

        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        downloaded = d.get("downloaded_bytes", 0)
        if not total:
            return

        percent = int(downloaded / total * 100)
        now = time.time()

        # Faqat foiz o'zgarganda va kamida 1.5 soniyadan keyin yangilaymiz
        if percent == progress_state["last_percent"]:
            return
        if percent < 100 and (now - progress_state["last_edit_time"] < 1.5):
            return

        progress_state["last_percent"] = percent
        progress_state["last_edit_time"] = now

        bar_filled = percent // 10
        bar = "▓" * bar_filled + "░" * (10 - bar_filled)

        async def edit():
            try:
                await query.edit_message_text(f"⏳ {label} yuklanmoqda...\n{bar} {percent}%")
            except Exception:
                pass

        asyncio.run_coroutine_threadsafe(edit(), loop)

    if choice == "video":
        ydl_opts = {
            'format': 'best[ext=mp4][height<=720]/best[height<=720]/best[ext=mp4]/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'noplaylist': True,
            'concurrent_fragment_downloads': 8,
            'progress_hooks': [progress_hook],
            'cookiefile': 'cookies.txt',  # <-- Cookie fayli ulandi
        }
    else:
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'noplaylist': True,
            'concurrent_fragment_downloads': 8,
            'progress_hooks': [progress_hook],
            'cookiefile': 'cookies.txt',  # <-- Cookie fayli ulandi
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }

    try:
        def do_download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                return info, filename

        info, filename = await asyncio.to_thread(do_download)

        if choice == "audio":
            base, _ = os.path.splitext(filename)
            filename = base + ".mp3"

        await query.edit_message_text(f"✅ {label} tayyor, yuborilmoqda...")

        title = info.get("title") or ""
        description = info.get("description") or ""

        caption_text = title
        if description:
            caption_text = f"{title}\n\n{description}"
        caption_text = caption_text.strip()[:1000]

        if not caption_text:
            caption_text = None

        if choice == "video":
            with open(filename, 'rb') as f:
                await context.bot.send_video(
                    chat_id=user_id,
                    video=f,
                    caption=caption_text,
                    supports_streaming=True
                )
        else:
            with open(filename, 'rb') as f:
                await context.bot.send_audio(
                    chat_id=user_id,
                    audio=f,
                    caption=caption_text,
                    title=title or None
                )

        os.remove(filename)
        await query.delete_message()

    except Exception as e:
        await query.edit_message_text(f"❌ Xatolik yuz berdi: {e}")


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_received))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
