import os
import http.server
import threading
import asyncio
import logging
import re
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright

# --- SERVER HTTP MINI UNTUK RENDER ---
class SimpleHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Pemantau Produk Aktif!")

    def log_message(self, format, *args):
        pass # Menonaktifkan log HTTP agar tampilan terminal tetap bersih

def start_server():
    port = int(os.environ.get("PORT", 10000))
    server = ('0.0.0.0', port)
    httpd = http.server.HTTPServer(server, SimpleHandler)
    httpd.serve_forever()

# Jalankan server mini di latar belakang agar Render mendeteksi port aktif
threading.Thread(target=start_server, daemon=True).start()

# --- KONFIGURASI ---
TELEGRAM_TOKEN = "9007524894:AAFBMq7K4_mEo48qRmH0lxoWP8e9Uq-NSOc"

# Basis data sederhana di dalam memori
monitored_products = [] 

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- SCRAPER MENGGUNAKAN PLAYWRIGHT ---
async def scrape_product(url):
    """Mengambil data judul, harga, dan status stok berdasarkan tautan"""
    platform = "Tidak Diketahui"
    if "shopee" in url:
        platform = "Shopee"
    elif "tokopedia" in url:
        platform = "Tokopedia"
    elif "tiktok" in url:
        platform = "TikTok Shop"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        try:
            # Menggunakan domcontentloaded agar proses pemuatan stabil
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(3) # Menunggu proses rendering JavaScript selesai

            title = await page.title()
            price = "Tidak terdeteksi"
            stock_status = "Tersedia"

            # 1. LOGIKA TOKOPEDIA
            if platform == "Tokopedia":
                price_elem = await page.query_selector('[data-testid="pdpProductPrice"]')
                if price_elem:
                    price = await price_elem.inner_text()
                
                out_of_stock = await page.query_selector('text="Stok Habis"')
                if out_of_stock:
                    stock_status = "Habis"

            # 2. LOGIKA SHOPEE
            elif platform == "Shopee":
                try:
                    # Menutup jendela penyelia (pop-up) masuk/login jika muncul
                    close_popup = await page.query_selector('.shopee-popup__close-btn, div[role="dialog"] button, .fa-xmark')
                    if close_popup:
                        await close_popup.click()
                        await asyncio.sleep(1)
                except:
                    pass

                price_elem = await page.query_selector('.pq8P62, ._1v5v_Y, span.partial-price')
                if price_elem:
                    price = await price_elem.inner_text()
                
                buy_button = await page.query_selector('.btn-solid-primary--disabled')
                if buy_button:
                    stock_status = "Habis"

            # 3. LOGIKA TIKTOK SHOP
            elif platform == "TikTok Shop":
                price_elem = await page.query_selector('[class*="price"], [class*="Price"]')
                if price_elem:
                    price = await price_elem.inner_text()

            await browser.close()
            return {
                "platform": platform,
                "title": title[:40] + "..." if title else "Produk E-Commerce",
                "price": price.strip() if price else "Tidak terdeteksi",
                "stock": stock_status,
                "status": True
            }

        except Exception as e:
            await browser.close()
            logging.error(f"Terjadi kesalahan saat mengecek {url}: {e}")
            return {"status": False, "error": str(e)}

# --- PENANGAN PERINTAH TELEGRAM ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Selamat Datang di Bot Pemantau Stok & Harga!**\n\n"
        "Silakan kirimkan tautan produk dari **Shopee**, **Tokopedia**, atau **TikTok Shop**.\n"
        "*(Disarankan menggunakan tautan lengkap/asli produk agar dapat diproses lebih cepat)*\n\n"
        "Bot akan mengecek harga dan stok produk secara otomatis setiap 15 menit.\n\n"
        "Daftar Perintah:\n"
        "/list - Melihat daftar produk yang sedang dipantau\n"
        "/clear - Menghapus seluruh daftar pantauan\n"
        "/restart - Menyegarkan sesi bot (daftar pantauan tetap tersimpan)\n"
        "/reboot - Mengatur ulang total bot (menghapus seluruh daftar pantauan)",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.message.chat_id

    urls = re.findall(r'(https?://[^\s]+)', text)
    if not urls:
        return

    url = urls[0]
    await update.message.reply_text("🔍 Memeriksa rincian produk... Mohon tunggu sebentar (Maksimal 5 menit).")
    
    try:
        # Membatasi durasi pemrosesan maksimal 300 detik (5 menit)
        data = await asyncio.wait_for(scrape_product(url), timeout=300.0)
    except asyncio.TimeoutError:
        await update.message.reply_text("❌ Pemrosesan memuat halaman terlalu lama (lebih dari 5 menit). Tautan gagal dimuat, silakan kirimkan kembali tautan yang valid.")
        return

    if data["status"]:
        monitored_products.append({
            "chat_id": chat_id,
            "url": url,
            "platform": data["platform"],
            "title": data["title"],
            "last_price": data["price"],
            "last_stock": data["stock"]
        })
        
        reply_msg = (
            f"✅ **Produk Berhasil Ditambahkan ke Pemantauan!**\n\n"
            f"📌 **Platform:** {data['platform']}\n"
            f"📦 **Nama Produk:** {data['title']}\n"
            f"💰 **Harga Saat Ini:** {data['price']}\n"
            f"📊 **Status Stok:** {data['stock']}\n\n"
            f"🔄 *Pengecekan ulang akan dilakukan secara otomatis setiap 15 menit.*"
        )
        await update.message.reply_text(reply_msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Gagal mengambil data produk. Mohon pastikan tautan produk valid atau gunakan tautan asli produk.")

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_items = [p for p in monitored_products if p["chat_id"] == chat_id]
    
    if not user_items:
        await update.message.reply_text("Belum ada produk yang dipantau.")
        return

    msg = "📋 **Daftar Produk yang Dipantau:**\n\n"
    for idx, item in enumerate(user_items, 1):
        msg += f"{idx}. [{item['platform']}] {item['title']}\n💰 {item['last_price']} | 📦 {item['last_stock']}\n🔗 {item['url']}\n\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitored_products
    chat_id = update.message.chat_id
    monitored_products = [p for p in monitored_products if p["chat_id"] != chat_id]
    await update.message.reply_text("🗑 Seluruh daftar pemantauan Anda telah berhasil dihapus.")

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_items = [p for p in monitored_products if p["chat_id"] == chat_id]
    
    await update.message.reply_text(
        f"🔄 **Sesi Bot Berhasil Diperbarui!**\n\n"
        f"Sesi bot telah disegarkan. Sebanyak **{len(user_items)} produk** yang sedang Anda pantau **tetap tersimpan** dan tidak dihapus.\n\n"
        f"📋 Ketik /list untuk melihat kembali daftar produk yang dipantau.",
        parse_mode="Markdown"
    )

async def reboot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitored_products
    chat_id = update.message.chat_id
    monitored_products = [p for p in monitored_products if p["chat_id"] != chat_id]
    
    await update.message.reply_text(
        "🔄 **Bot Berhasil Diatur Ulang (Reboot)!**\n\n"
        "Seluruh proses pemantauan Anda telah dibatalkan dan dihapus.\n\n"
        "👋 **Selamat Datang Kembali di Menu Utama Bot Pemantau Stok & Harga!**\n"
        "Silakan kirimkan tautan produk baru dari Shopee, Tokopedia, atau TikTok Shop untuk memulai pemantauan kembali.",
        parse_mode="Markdown"
    )

# --- PENGECEKAN BERKALA EVERY 15 MENIT ---
async def check_updates_job(app):
    if not monitored_products:
        return

    logging.info("Memulai pengecekan berkala 15 menit...")
    for item in monitored_products:
        try:
            # Memberikan batas waktu pemrosesan 5 menit pada pengecekan berkala
            new_data = await asyncio.wait_for(scrape_product(item["url"]), timeout=300.0)
        except asyncio.TimeoutError:
            logging.error(f"Batas waktu (5 menit) terlampaui saat mengecek produk: {item['url']}")
            continue

        if new_data["status"]:
            price_changed = new_data["price"] != item["last_price"]
            stock_changed = new_data["stock"] != item["last_stock"]

            if price_changed or stock_changed:
                alert_msg = (
                    f"🔔 **PERUBAHAN TERDETEKSI!**\n\n"
                    f"📦 **Nama Produk:** {item['title']}\n"
                    f"🏪 **Platform:** {item['platform']}\n\n"
                    f"💰 **Harga:** {item['last_price']} ➡️ *{new_data['price']}*\n"
                    f"📊 **Stok:** {item['last_stock']} ➡️ *{new_data['stock']}*\n\n"
                    f"🔗 [Klik Di Sini untuk Membeli]({item['url']})"
                )
                
                item["last_price"] = new_data["price"]
                item["last_stock"] = new_data["stock"]
                
                await app.bot.send_message(chat_id=item["chat_id"], text=alert_msg, parse_mode="Markdown")

async def schedule_checker(app):
    while True:
        await asyncio.sleep(900) # 15 Menit
        await check_updates_job(app)

# --- EKSEKUSI UTAMA ---
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("reboot", reboot_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    loop = asyncio.get_event_loop()
    loop.create_task(schedule_checker(app))

    print("Bot berjalan...")
    app.run_polling()
