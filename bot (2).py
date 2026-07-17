import os
import time
import asyncio
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

TOKEN = "TOKENINGIZNI_SHU_YERGA_YOZING"

user_links = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Salom! Menga YouTube havolasini yuboring — video yoki audio yuklab beraman 🎬🎵")

async def link_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("Iltimos, to'g'ri havola yuboring.")
        return
    user_links[update.effective_user.id] = url
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Video", callback_data="video"), InlineKeyboardButton("🎵 Audio", callback_data="audio")]
    ])
    await update.message.reply_text("Qaysi formatda yuklab beray?", reply_markup=keyboard)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    user_id = query.from_user.id
    url = user_links.get(user_id)

    if not url:
        await query.edit_message_text("Havola topilmadi, iltimos qaytadan yuboring.")
        return

    label = "Video" if choice == "video" else "Audio"
    await query.edit_message_text(f"⏳ {label} yuklanmoqda... 0%")
    os.makedirs("downloads", exist_ok=True)
    loop = asyncio.get_running_loop()
    progress_state = {"last_percent": -1, "last_edit_time": 0.0}

    def progress_hook(d):
        if d.get("status") != "downloading": return
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        downloaded = d.get("downloaded_bytes", 0)
        if not total: return
        percent = int(downloaded / total * 100)
        now = time.time()
        if percent == progress_state["last_percent"] or (now - progress_state["last_edit_time"] < 2): return
        progress_state["last_percent"] = percent
        progress_state["last_edit_time"] = now
        bar = "▓" * (percent // 10) + "░" * (10 - percent // 10)
        asyncio.run_coroutine_threadsafe(query.edit_message_text(f"⏳ {label} yuklanmoqda...\n{bar} {percent}%"), loop)

    # SOZLAMALAR (Xatolik bermasligi uchun universal format)
    ydl_opts = {
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'noplaylist': True,
        'concurrent_fragment_downloads': 8,
        'progress_hooks': [progress_hook],
        'cookiefile': 'cookies.txt', # Agar fayl bo'lsa
    }

    if choice == "video":
        ydl_opts['format'] = 'bestvideo+bestaudio/best' # Eng yaxshisini tanlaydi
    else:
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '128'}]

    try:
        def do_download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return info, ydl.prepare_filename(info)

        info, filename = await asyncio.to_thread(do_download)
        if choice == "audio":
            filename = os.path.splitext(filename)[0] + ".mp3"

        await query.edit_message_text(f"✅ {label} tayyor, yuborilmoqda...")
        
        # Hashtag qo'shish qismi
        caption = f"#{choice} #{info.get('title', 'video').replace(' ', '_')}"

        if choice == "video":
            await context.bot.send_video(chat_id=user_id, video=open(filename, 'rb'), caption=caption)
        else:
            await context.bot.send_audio(chat_id=user_id, audio=open(filename, 'rb'), caption=caption)
        
        if os.path.exists(filename): os.remove(filename)
        await query.delete_message()
    except Exception as e:
        await query.edit_message_text(f"❌ Xatolik yuz berdi: {e}")

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_received))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
