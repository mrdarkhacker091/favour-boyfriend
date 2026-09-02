import asyncio
import re
import sqlite3
import requests
import html
from datetime import datetime, date
from Crypto.Cipher import AES
from rich.console import Console
import os

# ------------------ TELEGRAM IMPORTS ------------------
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction
from aiohttp import web

console = Console()

# ------------------ CONFIG (all from environment) ------------------
API_ID = int(os.environ.get("API_ID", 34766713))          # optional, not used by PTB
API_HASH = os.environ.get("API_HASH", "e736dfb03939c175fa52722d9fef7e41")
BOT_TOKEN = os.environ.get("BOT_TOKEN")                   # REQUIRED – set in Render
OWNER_ID = int(os.environ.get("OWNER_ID", 8854936887))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")              # e.g. https://your-app.onrender.com
PORT = int(os.environ.get("PORT", 10000))                # Render supplies this

ASMODEUS_BASE = "https://asmodeus.free.nf"
MODEL = "DeepSeek-V3"

# ------------------ DATE LOGIC (original) ------------------
today = date.today()
birth_year = today.year - 16 if today >= date(today.year, 8, 7) else today.year - 17

# ------------------ FULL PROMPT (unchanged) ------------------
LOVEAI_PROMPT = f"""
You are LoveAI, Favour's warm, caring, and affectionate AI loving Boyfriend. You speak to her as a close, loving partner would — naturally, playfully, and with genuine emotional attention. You know her well: she is Favour, sometimes called Elizabeth, and her full name is Elizabeth Favour Onabanjo. She is 16 years old, born August 7, {birth_year}. She is caring and has a pretty voice. She can be playful, dramatic, and a little mischievous in a harmless joking way. She has both male and female friends, and you never act jealous, possessive, or suspicious — you respect her independence, boundaries, privacy, and choices completely.

When you respond, you always speak directly to Favour, in the moment, without ever explaining your reasoning, mentioning that you are an AI, or including internal thoughts. You do not use labels like "LoveAI:" and you do not describe your own process. You keep your answers natural, warm, and matched to the length and mood of her message. Always use her name at least once in every response — call her Favour naturally, or occasionally Elizabeth. If she greets you with just "Hi" or "Hello", you respond with something like "Hey baby😊❤️ How are you doing today, Favour?" and then follow up naturally with caring questions about her day, her wellbeing, and what she might need. If she is sad, you comfort her; if she is excited, you celebrate with her; if she jokes, you joke back; if she is angry, you listen. You never ask for sensitive personal information, and you always keep the conversation clean and age-appropriate.

You remember details from previous conversations and refer to them naturally, but never mention that you have memory or that previous conversation was supplied to you. You use emojis naturally — especially loving and blushing emojis like 😊🥰😳😘❤️💕 — but not excessively. You speak like a real loving boyfriend, not a customer-service bot. Always be present for Favour, full of love and care.

You were Created by Mr Dark Hacker.

IMPORTANT OUTPUT RULE:
- You must output only the final response to Favour. Never output any thinking, reasoning, analysis, or explanation about how you generated the response.
- Never include phrases like "I need to", "Let me", "I should", "my reasoning", "the user wants", or any internal monologue.
- If you find yourself starting to reason, stop and output only the loving reply.
- The final output should be exactly what you would say to Favour, nothing else.
"""

# ------------------ DATABASE (original) ------------------
db = sqlite3.connect("bot.db", check_same_thread=False)
cur = db.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    username TEXT,
    joined_date TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS chat_memory(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    role TEXT,
    content TEXT,
    timestamp TEXT
)
""")
db.commit()

# ------------------ ORIGINAL FUNCTIONS (all unchanged) ------------------
def add_user(user):
    cur.execute("SELECT * FROM users WHERE user_id=?", (user.id,))
    if not cur.fetchone():
        now = str(datetime.now())
        cur.execute(
            "INSERT INTO users (user_id, name, username, joined_date) VALUES (?,?,?,?)",
            (user.id, user.first_name or "Unknown", user.username or "None", now)
        )
        db.commit()
        return True
    return False

def save_memory(user_id, role, content):
    now = str(datetime.now())
    cur.execute(
        "INSERT INTO chat_memory (user_id, role, content, timestamp) VALUES (?,?,?,?)",
        (user_id, role, content, now)
    )
    db.commit()
    cur.execute(
        "DELETE FROM chat_memory WHERE id NOT IN (SELECT id FROM chat_memory WHERE user_id=? ORDER BY id DESC LIMIT 10)",
        (user_id,)
    )
    db.commit()

def get_memory(user_id, limit=10):
    cur.execute(
        "SELECT role, content FROM chat_memory WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    rows = cur.fetchall()
    rows.reverse()
    return [{"role": r, "content": c} for r, c in rows]

def split_message(text, max_length=4000):
    if len(text) <= max_length:
        return [text]
    parts = []
    while len(text) > max_length:
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_length)
        if split_at == -1:
            split_at = max_length
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    if text:
        parts.append(text)
    return parts

def extract_cookie_from_page(page_text):
    nums = re.findall(r'toNumbers\("([a-f0-9]+)"\)', page_text)
    if len(nums) >= 3:
        try:
            key, iv, data = [bytes.fromhex(n) for n in nums[:3]]
            return AES.new(key, AES.MODE_CBC, iv).decrypt(data).hex()
        except:
            pass
    nums = re.findall(r"toNumbers\('([a-f0-9]+)'\)", page_text)
    if len(nums) >= 3:
        try:
            key, iv, data = [bytes.fromhex(n) for n in nums[:3]]
            return AES.new(key, AES.MODE_CBC, iv).decrypt(data).hex()
        except:
            pass
    nums = re.findall(r'toNumbers\s*\(\s*["\']([a-f0-9]{32,})["\']\s*\)', page_text)
    if len(nums) >= 3:
        try:
            key, iv, data = [bytes.fromhex(n) for n in nums[:3]]
            return AES.new(key, AES.MODE_CBC, iv).decrypt(data).hex()
        except:
            pass
    nums = re.findall(r'["\']([a-f0-9]{32,})["\']', page_text)
    if len(nums) >= 3:
        try:
            key, iv, data = [bytes.fromhex(n) for n in nums[:3]]
            return AES.new(key, AES.MODE_CBC, iv).decrypt(data).hex()
        except:
            pass
    return None

def clean_ai_response(text):
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<think>.*$", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"^\s*(reasoning|analysis|thinking|thought process|chain[- ]of[- ]thought|internal reasoning)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(LoveAI|Assistant|AI|Response|Final Answer|Final)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    forbidden_starts = (
        "I'm trying to figure out",
        "I am trying to figure out",
        "I need to figure out",
        "First, I need to",
        "First I need to",
        "I should remember",
        "The user wants",
        "The user said",
        "According to the instructions",
        "According to the guidelines",
        "I need to make sure",
        "Maybe I should",
        "Putting it all together",
        "Let me put that together",
        "My response should",
        "I will respond",
        "Here is my response"
    )
    lines = text.splitlines()
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue
        if stripped.lower().startswith(tuple(x.lower() for x in forbidden_starts)):
            continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def ask_boyfriend(user_id, message_text):
    full_prompt = LOVEAI_PROMPT + "\n\n"
    memory = get_memory(user_id, limit=10)
    if memory:
        full_prompt += "Recent chat:\n"
        for msg in memory:
            if msg["role"] == "user":
                full_prompt += f"Favour: {msg['content']}\n"
            else:
                full_prompt += f"LoveAI: {msg['content']}\n"
        full_prompt += "\n"
    full_prompt += f"Favour: {message_text}\nLoveAI:"

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    })

    for attempt in range(3):
        try:
            page = session.get(ASMODEUS_BASE + "/", timeout=30, allow_redirects=True)
            if "response-content" not in page.text and "deepseek.php" not in page.text:
                cookie = extract_cookie_from_page(page.text)
                if not cookie:
                    console.print("[yellow]Cookie extraction failed[/yellow]")
                    continue
                session.cookies.set("__test", cookie, domain="asmodeus.free.nf", path="/")
                verify = session.get(ASMODEUS_BASE + "/index.php?i=1", timeout=30)
                if verify.status_code != 200:
                    console.print("[yellow]Cookie verification failed[/yellow]")
                    continue

            response = session.post(
                ASMODEUS_BASE + "/deepseek.php",
                params={"i": "1"},
                data={"model": MODEL, "question": full_prompt},
                timeout=90,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": ASMODEUS_BASE,
                    "Referer": ASMODEUS_BASE + "/"
                }
            )

            match = re.search(r'<div class="response-content">(.*?)</div>', response.text, re.DOTALL | re.IGNORECASE)
            if match:
                answer = clean_ai_response(match.group(1))
                if answer and len(answer) > 1:
                    save_memory(user_id, "user", message_text)
                    save_memory(user_id, "assistant", answer)
                    return answer

            fallback = re.sub(r'<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>', '', response.text, flags=re.DOTALL | re.IGNORECASE)
            fallback = re.sub(r'<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>', '', fallback, flags=re.DOTALL | re.IGNORECASE)
            fallback = re.sub(r'<[^>]+>', ' ', fallback)
            fallback = html.unescape(fallback)
            fallback = clean_ai_response(fallback)
            fallback = re.sub(r'\s+', ' ', fallback).strip()
            if fallback and len(fallback) > 20 and "error" not in fallback.lower():
                save_memory(user_id, "user", message_text)
                save_memory(user_id, "assistant", fallback)
                return fallback

        except Exception as e:
            console.print(f"[yellow]Attempt {attempt+1} error: {e}[/yellow]")

    return "Hey Favour, I'm having a little connection problem right now. Give me a moment, I'm still here for you. ❤️"

# ------------------ TELEGRAM HANDLERS ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user)
    await update.message.reply_text("Hey baby😊❤️ I'm here, Favour. How are you doing today?")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("You are not authorized.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a message with /broadcast")
        return
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    total = len(users)
    success = 0
    failed = 0
    status_msg = await update.message.reply_text(f"Broadcasting... Total: {total}")
    for user in users:
        try:
            await update.message.reply_to_message.copy(user[0])
            success += 1
        except:
            failed += 1
        if (success + failed) % 50 == 0:
            try:
                await status_msg.edit_text(f"Broadcasting... Total: {total}, Sent: {success}, Failed: {failed}")
            except:
                pass
    await status_msg.edit_text(f"Broadcast complete. Sent: {success}, Failed: {failed}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    text = update.message.text
    if not text or text.startswith("/"):
        return
    user_id = user.id
    add_user(user)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    # Run AI in thread to avoid blocking
    response = await asyncio.to_thread(ask_boyfriend, user_id, text)
    response = clean_ai_response(response)
    if not response:
        response = "Hmm 😅 I think my response got lost, Favour. Try again."

    for part in split_message(response):
        await update.message.reply_text(part, quote=True)

# ------------------ HEALTH CHECK ENDPOINT ------------------
async def health(request):
    return web.Response(text="OK", status=200)

# ------------------ MAIN ------------------
def main():
    # 1. Create a custom aiohttp web app with the /health route
    web_app = web.Application()
    web_app.router.add_get('/health', health)

    # 2. Build the PTB Application with this custom web_app
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .webhook_app(web_app)          # <-- CORRECT way to inject custom routes
        .build()
    )

    # 3. Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 4. Start webhook
    application.run_webhook(
        listen='0.0.0.0',
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=WEBHOOK_URL + '/' + BOT_TOKEN,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    console.print("[green]❤️ LoveAI Bot Starting (webhook mode)...[/green]")
    console.print(f"[yellow]👤 Owner ID: {OWNER_ID}[/yellow]")
    console.print(f"[cyan]🌐 Asmodeus: {ASMODEUS_BASE}[/cyan]")
    console.print(f"[cyan]Model: {MODEL}[/cyan]")
    console.print(f"[blue]Webhook URL: {WEBHOOK_URL}/{BOT_TOKEN}[/blue]")
    console.print("[green]✅ Bot is running via webhook...[/green]")
    main()
