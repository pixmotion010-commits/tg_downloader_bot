import os
import time
import asyncio
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

TOKEN = "8895598746:AAEjMBu3eED7CVEoOjSuZhTU2IVr2e6Wo3w"

# Render'da "Secret Files" orqali yuklangan cookies.txt shu manzilda joylashadi.
# Agar u yerda topilmasa, joriy papkadan (masalan kompyuteringizda ishlatganda) qidiramiz.
_RENDER_COOKIES_PATH = "/etc/secrets/cookies.txt"
_LOCAL_COOKIES_PATH = "cookies.txt"


def _find_valid_cookies_path():
    """Cookies faylini topadi, tekshiradi va YOZISH MUMKIN bo'lgan joyga (/tmp) nusxalaydi.
    Bu muhim, chunki Render'ning 'Secret Files' papkasi (/etc/secrets) faqat o'qish uchun,
    yt-dlp esa cookies faylini ishlatgandan keyin uni yangilab qayta yozishga harakat qiladi.
    Agar fayl topilmasa yoki buzilgan bo'lsa, None qaytaradi - shunda bot cookies'siz ishlaydi."""
    import http.cookiejar
    import shutil

    for path in (_RENDER_COOKIES_PATH, _LOCAL_COOKIES_PATH):
        if not os.path.exists(path):
            continue
        try:
            # Avval original faylni tekshiramiz (formati to'g'rimi)
            jar = http.cookiejar.MozillaCookieJar(path)
            jar.load(ignore_discard=True, ignore_expires=True)

            # Yozish mumkin bo'lgan vaqtinchalik joyga nusxalaymiz
            writable_path = "/tmp/cookies_writable.txt"
            shutil.copyfile(path, writable_path)

            print(f"Cookies fayli topildi, tekshirildi va nusxalandi: {path} -> {writable_path} ({len(jar)} ta cookie)")
            return writable_path
        except Exception as e:
            print(f"OGOHLANTIRISH: cookies fayli topildi ({path}) lekin tayyorlab bo'lmadi: {e}")
            continue

    print("OGOHLANTIRISH: yaroqli cookies.txt topilmadi. Bot cookies'siz ishlaydi.")
    return None


COOKIES_PATH = _find_valid_cookies_path()

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
        # (Telegram API'ni haddan tashqari ko'p chaqirmaslik uchun)
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
                pass  # xabar o'zgarmagan yoki kichik xato bo'lsa e'tiborsiz qoldiramiz

        # Bu funksiya alohida thread'da ishlaydi, shuning uchun asosiy event loop'ga
        # xavfsiz tarzda vazifa yuboramiz
        asyncio.run_coroutine_threadsafe(edit(), loop)

    def postprocessor_hook(d):
        # Audio'ga aylantirish (FFmpeg) jarayoni boshlanganda alohida xabar ko'rsatamiz
        if d.get("status") == "started":
            async def edit():
                try:
                    await query.edit_message_text("🔄 Audio'ga aylantirilmoqda, biroz kuting...")
                except Exception:
                    pass
            asyncio.run_coroutine_threadsafe(edit(), loop)

    if choice == "video":
        ydl_opts = {
            # 720p gacha cheklaymiz - tezroq yuklanadi, sifat baribir yaxshi
            'format': 'best[ext=mp4][height<=720]/best[height<=720]/best[ext=mp4]/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'noplaylist': True,
            'concurrent_fragment_downloads': 8,  # parallel yuklash - tezroq
            'progress_hooks': [progress_hook],
            'cookiefile': COOKIES_PATH,
        }
    else:
        ydl_opts = {
            # Faqat audio oqimini yuklaymiz (butun videoni emas) - ancha tezroq
            'format': 'bestaudio[abr<=128]/bestaudio/best',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'noplaylist': True,
            'concurrent_fragment_downloads': 8,
            'progress_hooks': [progress_hook],
            'postprocessor_hooks': [postprocessor_hook],
            'cookiefile': COOKIES_PATH,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '128',  # 192 emas 128 - tezroq konvertatsiya, sifat baribir yaxshi
            }],
        }

    filename = None
    try:
        # yt-dlp yuklash jarayoni "bloklovchi" (sync) bo'lgani uchun,
        # botning boshqa foydalanuvchilarga javob berishini to'xtatib qo'ymasligi uchun
        # uni alohida thread'da ishga tushiramiz
        def do_download(use_cookies=True):
            opts = dict(ydl_opts)
            if not use_cookies:
                opts.pop('cookiefile', None)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                fname = ydl.prepare_filename(info)
                return info, fname

        try:
            info, filename = await asyncio.to_thread(do_download, True)
        except Exception as first_error:
            # Agar xato cookies fayli bilan bog'liq bo'lishi mumkin bo'lsa
            # (masalan fayl formatida yoki yozish huquqida muammo), cookies'siz qayta urinamiz
            error_lower = str(first_error).lower()
            cookie_related = (
                "cookie" in error_lower
                or "read-only" in error_lower
                or "errno 30" in error_lower
            )
            if COOKIES_PATH and cookie_related:
                print(f"Cookies bilan xato chiqdi, cookies'siz qayta urinilmoqda: {first_error}")
                info, filename = await asyncio.to_thread(do_download, False)
            else:
                raise

        # Audio holatida fayl kengaytmasi mp3'ga o'zgaradi
        if choice == "audio":
            base, _ = os.path.splitext(filename)
            filename = base + ".mp3"

        await query.edit_message_text(f"✅ {label} tayyor, yuborilmoqda...")

        # Original post sarlavhasi va tavsifini (hashtaglar shu ichida) caption sifatida olamiz
        title = info.get("title") or ""
        description = info.get("description") or ""

        caption_text = title
        if description:
            caption_text = f"{title}\n\n{description}"
        caption_text = caption_text.strip()[:1000]  # Telegram caption limiti ~1024

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
        error_text = str(e)

        # Fayl hajmi juda katta bo'lsa (Telegram bot API limiti ~50MB), tushunarli xabar beramiz
        if "too large" in error_text.lower() or "Request Entity Too Large" in error_text:
            friendly = "❌ Fayl juda katta (Telegram bot orqali 50MB dan katta fayl yuborib bo'lmaydi)."
        else:
            # Xatolik matni juda uzun bo'lsa qisqartiramiz (Telegram xabar limiti ~4096 belgi)
            short_error = error_text[:300]
            friendly = f"❌ Xatolik yuz berdi: {short_error}"

        try:
            await query.edit_message_text(friendly)
        except Exception:
            # Agar xabarni tahrirlab bo'lmasa (masalan juda ko'p vaqt o'tgan bo'lsa), yangi xabar yuboramiz
            try:
                await context.bot.send_message(chat_id=user_id, text=friendly)
            except Exception:
                pass

        # Yuklab olingan, lekin yuborilmagan qolgan faylni tozalaymiz (agar mavjud bo'lsa)
        try:
            if 'filename' in locals() and filename and os.path.exists(filename):
                os.remove(filename)
        except Exception:
            pass


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_received))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
