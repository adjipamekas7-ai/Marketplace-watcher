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
        pass # Menonaktifkan log HTTP agar terminal bersih

def start_server():
    port = int(os.environ.get("PORT", 10000))
    server = ('0.0.0.0', port)
    httpd = http.server.HTTPServer(server, SimpleHandler)
    httpd.serve_forever()

# Jalankan server mini di background agar Render mendeteksi port aktif
threading.Thread(target=start_server, daemon=True).start()

# --- KONFIGURASI ---
TELEGRAM_TOKEN = "9007524894:AAFBMq7K4_mEo48qRmH0lxoWP8e9Uq-NSOc"

# Database ringkas di dalam memori
monitored_products = [] 

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- SCRAPER MENGGUNAKAN PLAYWRIGHT ---
async def scrape_product(url):
    """Scrape tajuk, harga, dan status stok berdasarkan pautan"""
    platform = "Unknown"
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
            # Menggunakan domcontentloaded agar stabil dan tidak mudah tersangkut
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(3) # Tunggu rendering JS selesai

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
                price_elem = await page.query_selector('.pq8P62, ._1v5v_Y')
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
            logging.error(f"Error scraping {url}: {e}")
            return {"status": False, "error": str(e)}

# --- HANDLER BOT TELEGRAM ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Selamat Datang di Bot Pemantau Stok & Harga!**\n\n"
        "Kirimkan pautan produk dari **Shopee**, **Tokopedia**, atau **TikTok Shop**.\n"
        "*(Disarankan menggunakan pautan panjang/asli produk agar lebih pantas dibaca)*\n\n"
        "Saya akan menyemak harga & stoknya secara automatik setiap 15 minit!\n\n"
        "Perintah:\n"
        "/list - Lihat senarai produk dipantau\n"
        "/clear - Padam semua pantauan\n"
        "/restart - Segarkan sesi bot (pantauan tetap selamat)\n"
        "/reboot - Tetap semula total bot (padam semua pantauan)",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.message.chat_id

    urls = re.findall(r'(https?://[^\s]+)', text)
    if not urls:
        return

    url = urls[0]
    await update.message.reply_text("🔍 Memeriksa terperinci produk... Sila tunggu sebentar.")
    
    data = await scrape_product(url)
    
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
            f"✅ **Produk Berjaya Ditambah ke Pemantauan!**\n\n"
            f"📌 **Platform:** {data['platform']}\n"
            f"📦 **Produk:** {data['title']}\n"
            f"💰 **Harga Semasa:** {data['price']}\n"
            f"📊 **Stok:** {data['stock']}\n\n"
            f"🔄 *Semakan semula akan dilakukan setiap 15 minit.*"
        )
        await update.message.reply_text(reply_msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Gagal mendapatkan data produk. Sila pastikan pautan produk sah atau guna pautan asal.")

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_items = [p for p in monitored_products if p["chat_id"] == chat_id]
    
    if not user_items:
        await update.message.reply_text("Belum ada produk yang dipantau.")
        return

    msg = "📋 **Senarai Produk Dipantau:**\n\n"
    for idx, item in enumerate(user_items, 1):
        msg += f"{idx}. [{item['platform']}] {item['title']}\n💰 {item['last_price']} | 📦 {item['last_stock']}\n🔗 {item['url']}\n\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitored_products
    chat_id = update.message.chat_id
    monitored_products = [p for p in monitored_products if p["chat_id"] != chat_id]
    await update.message.reply_text("🗑 Semua senarai pantauan anda telah dipadam.")

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_items = [p for p in monitored_products if p["chat_id"] == chat_id]
    
    await update.message.reply_text(
        f"🔄 **Bot Berjaya di-Restart!**\n\n"
        f"Sesi bot telah disegarkan. Jangan bimbang, **{len(user_items)} produk** yang sedang anda pantau **kekal selamat** dan tidak dipadam.\n\n"
        f"📋 Taip /list untuk melihat kembali senarai produk yang dipantau.",
        parse_mode="Markdown"
    )

async def reboot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitored_products
    chat_id = update.message.chat_id
    monitored_products = [p for p in monitored_products if p["chat_id"] != chat_id]
    
    await update.message.reply_text(
        "🔄 **Bot Berjaya di-Reboot!**\n\n"
        "Semua proses pemantauan anda telah dibatalkan dan dipadam.\n\n"
        "👋 **Selamat Datang Kembali ke Menu Utama Bot Pemantau Stok & Harga!**\n"
        "Kirimkan pautan produk baharu dari Shopee, Tokopedia, atau TikTok Shop untuk mula memantau semula.",
        parse_mode="Markdown"
    )

# --- JOB BERKALA 15 MINIT ---
async def check_updates_job(app):
    if not monitored_products:
        return

    logging.info("Memulakan semakan berkala 15 minit...")
    for item in monitored_products:
        new_data = await scrape_product(item["url"])
        if new_data["status"]:
            price_changed = new_data["price"] != item["last_price"]
            stock_changed = new_data["stock"] != item["last_stock"]

            if price_changed or stock_changed:
                alert_msg = (
                    f"🔔 **PERUBAHAN DIKESAN!**\n\n"
                    f"📦 **Produk:** {item['title']}\n"
                    f"🏪 **Platform:** {item['platform']}\n\n"
                    f"💰 **Harga:** {item['last_price']} ➡️ *{new_data['price']}*\n"
                    f"📊 **Stok:** {item['last_stock']} ➡️ *{new_data['stock']}*\n\n"
                    f"🔗 [Klik untuk Beli]({item['url']})"
                )
                
                item["last_price"] = new_data["price"]
                item["last_stock"] = new_data["stock"]
                
                await app.bot.send_message(chat_id=item["chat_id"], text=alert_msg, parse_mode="Markdown")

async def schedule_checker(app):
    while True:
        await asyncio.sleep(900) # 15 Minit
        await check_updates_job(app)

# --- MAIN EXECUTION ---
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