import os
import http.server
import threading

# Server HTTP mini agar Render mendeteksi port aktif
class SimpleHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Pemantau Produk Aktif!")

def start_server():
    port = int(os.environ.get("PORT", 10000))
    server = ('0.0.0.0', port)
    httpd = http.server.HTTPServer(server, SimpleHandler)
    httpd.serve_forever()

# Jalankan server mini di background
threading.Thread(target=start_server, daemon=True).start()

import asyncio
import logging
import re
import time
import schedule
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright

# --- KONFIGURASI ---
TELEGRAM_TOKEN = "9007524894:AAFBMq7K4_mEo48qRmH0lxoWP8e9Uq-NSOc"
MY_CHAT_ID = "GANTI_DENGAN_CHAT_ID_ANDA" # Atau biarkan bot mengirim ke chat tempat link ditambahkan

# Database sederhana di memori (Gunakan SQLite/JSON jika ingin tersimpan permanen saat restart)
monitored_products = [] 
# Format: [{'url': '...', 'platform': '...', 'last_price': '...', 'last_stock': '...', 'chat_id': 12345}]

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- SCRAPER MENGGUNAKAN PLAYWRIGHT ---
async def scrape_product(url):
    """Scrape judul, harga, dan status stok berdasarkan link"""
    platform = "Unknown"
    if "shopee" in url:
        platform = "Shopee"
    elif "tokopedia" in url:
        platform = "Tokopedia"
    elif "tiktok" in url:
        platform = "TikTok Shop"

    async with async_playwright() as p:
        # Menjalankan headless browser dengan User-Agent browser asli
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        try:
            await page.goto(url, timeout=60000, wait_until="networkidle")
            await asyncio.sleep(3) # Tunggu rendering JS selesai

            title = await page.title()
            price = "Tidak terdeteksi"
            stock_status = "Tersedia"

            # 1. LOGIKA TOKOPEDIA
            if platform == "Tokopedia":
                price_elem = await page.query_selector('[data-testid="pdpProductPrice"]')
                if price_elem:
                    price = await price_elem.inner_text()
                
                # Cek stok habis
                out_of_stock = await page.query_selector('text="Stok Habis"')
                if out_of_stock:
                    stock_status = "Habis"

            # 2. LOGIKA SHOPEE
            elif platform == "Shopee":
                price_elem = await page.query_selector('.pq8P62, ._1v5v_Y')
                if price_elem:
                    price = await price_elem.inner_text()
                
                # Cek tombol beli/stok
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
                "title": title[:40] + "...",
                "price": price.strip(),
                "stock": stock_status,
                "status": True
            }

        except Exception as e:
            await browser.close()
            logging.error(f"Error scraping {url}: {e}")
            return {"status": False, "error": str(e)}

# --- HANDLER BOT TELEGRAM ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Selamat Datang di Bot Pemantau Stok & Harga!**\n\n"
        "Kirimkan link produk dari **Shopee**, **Tokopedia**, atau **TikTok Shop**.\n"
        "Saya akan mengecek harga & stoknya secara otomatis setiap 15 menit!\n\n"
        "Perintah:\n"
        "/list - Lihat daftar produk dipantau\n"
        "/clear - Hapus semua pantauan"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.message.chat_id

    # Filter URL
    urls = re.findall(r'(https?://[^\s]+)', text)
    if not urls:
        await update.message.reply_text("Silakan kirimkan URL/Link produk yang valid.")
        return

    url = urls[0]
    await update.message.reply_text("🔍 Memeriksa detail produk... Mohon tunggu sebentar.")
    
    # Scrape awal
    data = await scrape_product(url)
    
    if data["status"]:
        # Simpan ke database pemantauan
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
            f"📦 **Produk:** {data['title']}\n"
            f"💰 **Harga Saat Ini:** {data['price']}\n"
            f"📊 **Stok:** {data['stock']}\n\n"
            f"🔄 *Pengecekan ulang akan dilakukan setiap 15 menit.*"
        )
        await update.message.reply_text(reply_msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Gagal mengambil data produk. Pastikan link valid atau coba lagi nanti.")

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_items = [p for p in monitored_products if p["chat_id"] == chat_id]
    
    if not user_items:
        await update.message.reply_text("Belum ada produk yang dipantau.")
        return

    msg = "📋 **Daftar Produk Dipantau:**\n\n"
    for idx, item in enumerate(user_items, 1):
        msg += f"{idx}. [{item['platform']}] {item['title']}\n💰 {item['last_price']} | 📦 {item['last_stock']}\n🔗 {item['url']}\n\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitored_products
    chat_id = update.message.chat_id
    monitored_products = [p for p in monitored_products if p["chat_id"] != chat_id]
    await update.message.reply_text("🗑 Semua daftar pemantauan Anda telah dihapus.")

# --- JOB ATURAN 15 MENIT ---
async def check_updates_job(app):
    """Fungsi berkala untuk mengecek perubahan harga & stok"""
    if not monitored_products:
        return

    logging.info("Memulai pengecekan berkala 15 menit...")
    for item in monitored_products:
        new_data = await scrape_product(item["url"])
        if new_data["status"]:
            price_changed = new_data["price"] != item["last_price"]
            stock_changed = new_data["stock"] != item["last_stock"]

            # Kirim notifikasi jika ada perubahan harga atau stok
            if price_changed or stock_changed:
                alert_msg = (
                    f"🔔 **PERUBAHAN TERDETEKSI!**\n\n"
                    f"📦 **Produk:** {item['title']}\n"
                    f"🏪 **Platform:** {item['platform']}\n\n"
                    f"💰 **Harga:** {item['last_price']} ➡️ *{new_data['price']}*\n"
                    f"📊 **Stok:** {item['last_stock']} ➡️ *{new_data['stock']}*\n\n"
                    f"🔗 [Klik untuk Beli]({item['url']})"
                )
                
                # Update data terbaru
                item["last_price"] = new_data["price"]
                item["last_stock"] = new_data["stock"]
                
                await app.bot.send_message(chat_id=item["chat_id"], text=alert_msg, parse_mode="Markdown")

async def schedule_checker(app):
    """Loop interval 15 menit (900 detik)"""
    while True:
        await asyncio.sleep(900) # 15 Menit
        await check_updates_job(app)

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Jalankan background scheduler bersamaan dengan Telegram Bot
    loop = asyncio.get_event_loop()
    loop.create_task(schedule_checker(app))

    print("Bot berjalan...")
    app.run_polling()
