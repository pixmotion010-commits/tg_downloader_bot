import os
import re
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import yt_dlp

TOKEN = "8965637635:AAGEBE306sMTGvuVUxa6ReU_V2UR3JTcrFg"

# Yuklash jarayonini kuzatish (Progress bar)
def progress_hook(d):
    if d['status'] == 'downloading':
        pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men YouTube'dan video va audio yuklab beruvchi botman.\n"
        "Menga shunchaki YouTube video havolasini (linkini) yuboring."
    )

async def link_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    
    # YouTube havolasi ekanligini tekshirish
    youtube_regex = r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+'
    if not re.match(youtube_regex, url):
        await update.message.reply_text("Iltimos, faqat to'g'ri YouTube havolasini yuboring!")
        return

    context.user_data['url'] = url

    keyboard = [
        [
            InlineKeyboardButton("🎬 Video", callback_data="video"),
            InlineKeyboardButton("🎵 Audio (MP3)", callback_data="audio")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Formatni tanlang:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    choice = query.data
    url = context.user_data.get('url')

    if not url:
        await query.edit_message_text("Xatolik: Havola topilmadi. Iltimos, linkni qaytadan yuboring.")
        return

    label = "Video" if choice == "video" else "Audio"
    await query.edit_message_text(f"⏳ {label} yuklab olinmoqda, iltimos kuting...")

    # Yuklab olish papkasini yaratish
    if not os.path.exists('downloads'):
        os.makedirs('downloads')

    # yt-dlp sozlamalari
    if choice == "video":
        ydl_opts = {
            'format': 'best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'noplaylist': True,
            'concurrent_fragment_downloads': 8,
            'progress_hooks': [progress_hook],
            'cookiefile': 'cookies.txt',
        }
    else:
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'noplaylist': True,
            'concurrent_fragment_downloads': 8,
            'progress_hooks': [progress_hook],
            'cookiefile': 'cookies.txt',
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

        title = info.get("title") or "Video"
        description = info.get("description") or ""
        caption_text = title

        chat_id = update.effective_chat.id

        if choice == "video":
            with open(filename, 'rb') as f:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    caption=caption_text,
                    supports_streaming=True
                )
        else:
            with open(filename, 'rb') as f:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=f,
                    caption=caption_text,
                    title=title
                )

        if os.path.exists(filename):
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
