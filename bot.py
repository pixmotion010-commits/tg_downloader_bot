import os
import logging
import glob
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, CommandHandler, ContextTypes
import yt_dlp

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Tokenni shu yerga yozing
TOKEN = "8895598746:AAGcsyp2iq7bvzDbXyOzz1ibq4fazks-kjc"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🖐 Salom! YouTube va Instagram'dan video yuklovchi bot. Link yuboring.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    is_youtube = "youtube.com" in url or "youtu.be" in url
    is_instagram = "instagram.com" in url
    
    if is_youtube or is_instagram:
        status_message = await update.message.reply_text("📥 Video yuklanmoqda, kuting...")
        output_filename = f"video_{update.message.chat_id}.mp4"
        
        cookie_files = glob.glob("*cookie*.txt")
        cookie_file = cookie_files[0] if cookie_files else None
        
        ydl_opts = {
            'format': 'mp4/best', 
            'outtmpl': output_filename,
            'quiet': True,
            'noplaylist': True,
            'no_warnings': True,
        }
        
        if cookie_file:
            ydl_opts['cookiefile'] = cookie_file
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Video ma'lumotlarini yuklab olish
                info = ydl.extract_info(url, download=True)
                
                # Instagram yoki YouTube'dagi asl post matnini va xeshteglarini olish
                original_caption = info.get('description') or info.get('title') or ""
            
            await status_message.edit_text("📤 Video Telegram'ga yuborilmoqda...")
            
            # Agar matn juda uzun bo'lsa, Telegram limiti (1024 belgi) uchun kesish
            if len(original_caption) > 1000:
                original_caption = original_caption[:1000] + "..."
                
            with open(output_filename, 'rb') as video_file:
                await update.message.reply_video(
                    video=video_file, 
                    caption=original_caption  # Asl matn va heshteglar shu yerda ketadi
                )
            
            if os.path.exists(output_filename):
                os.remove(output_filename)
            await status_message.delete()
            
        except Exception as e:
            logger.error(f"Xatolik: {str(e)}")
            await status_message.edit_text(f"❌ Xatolik: {str(e)[:100]}")
            if os.path.exists(output_filename):
                os.remove(output_filename)
    else:
        await update.message.reply_text("⚠️ Faqat YouTube yoki Instagram havola yuboring.")

if __name__ == '__main__':
    if TOKEN == "BU_YERGA_BOTFATHERDAN_OLGAN_TOKENINGIZNI_YOZING":
        print("XATOLIK: Tokenni kiriting!")
    else:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        app.run_polling()
