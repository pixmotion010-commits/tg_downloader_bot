import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, CommandHandler, ContextTypes
import yt_dlp

# Loglarni sozlash (Render konsolida xatolarni kuzatish uchun)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# DIQQAT: Bot Tokeningizni quyidagi qo'shtirnoq ichiga yozing!
TOKEN = "8895598746:AAGcsyp2iq7bvzDbXyOzz1ibq4fazks-kjc"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🖐 Salom! Men YouTube va Instagram videolarini yuklab beruvchi botman.\n\n"
        "Menga YouTube yoki Instagram video havolasini (linkini) yuboring."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    
    # YouTube yoki Instagram havolasi ekanligini tekshirish
    is_youtube = "youtube.com" in url or "youtu.be" in url
    is_instagram = "instagram.com" in url
    
    if is_youtube or is_instagram:
        status_message = await update.message.reply_text("📥 Video tahlil qilinmoqda va yuklanmoqda, iltimos kuting...")
        
        # Har bir foydalanuvchi uchun alohida nomli vaqtinchalik fayl
        output_filename = f"video_{update.message.chat_id}.mp4"
        
        ydl_opts = {
            'format': 'best[ext=mp4]/best',  # Eng sifatli MP4 formatini yuklash
            'cookiefile': 'cookies.txt',     # Bloklardan o'tish uchun kalit fayl
            'outtmpl': output_filename,      # Fayl nomi
            'quiet': True,
            'noplaylist': True,              # Pleylist bo'lsa, faqat bitta videoni yuklash
        }
        
        try:
            # Videoni serverga yuklab olish
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            
            # Videoni foydalanuvchiga yuborish
            await status_message.edit_text("📤 Video Telegram'ga yuborilmoqda...")
            
            # Video ostiga yoziladigan matn va xeshteg
            caption_text = "✨ @oltinkuy_bot orqali yuklab olindi\n\n#OltinKuy"
            
            with open(output_filename, 'rb') as video_file:
                await update.message.reply_video(
                    video=video_file,
                    caption=caption_text
                )
            
            # Render xotirasi to'lib qolmasligi uchun faylni o'chirish
            if os.path.exists(output_filename):
                os.remove(output_filename)
                
            await status_message.delete()
            
        except Exception as e:
            logger.error(f"Xatolik yuz berdi: {str(e)}")
            await status_message.edit_text(
                "❌ Xatolik yuz berdi! Platforma yuklashni rad etdi.\n\n"
                "Agarda YouTube yuklamayotgan bo'lsa, `cookies.txt` faylingiz eskirgan bo'lishi mumkin. "
                "Instagram'da esa ba'zi yopiq profildagi videolarni yuklab bo'lmasligi mumkin."
            )
            if os.path.exists(output_filename):
                os.remove(output_filename)
    else:
        await update.message.reply_text("⚠️ Iltimos, faqat to'g'ri YouTube yoki Instagram havola yuboring.")

if __name__ == '__main__':
    if TOKEN == "BU_YERGA_BOTFATHERDAN_OLGAN_TOKENINGIZNI_YOZING":
        print("XATOLIK: Iltimos, kod ichiga bot tokeningizni yozing!")
    else:
        app = ApplicationBuilder().token(TOKEN).build()
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        
        print("Bot muvaffaqiyatli ishga tushdi...")
        app.run_polling()
