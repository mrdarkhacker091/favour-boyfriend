import asyncio

# Fix for Pyrogram + Python 3.12+ / 3.14
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import re
import sqlite3
import requests
import html
import random
import threading
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from Crypto.Cipher import AES
from pyrogram import Client, filters, idle
from pyrogram.enums import ChatAction
from rich.console import Console

# ------------------ Hardcoded Credentials (as per your request) ------------------
API_ID = 34766713
API_HASH = "e736dfb03939c175fa52722d9fef7e41"
BOT_TOKEN = "8619061450:AAFhR2cEgs97WFGJxnN8hGB-OW4XtDu6isY"
OWNER_ID = 8854936887
FAVOUR_USER_ID = 8854936887

ASMODEUS_BASE = "https://asmodeus.free.nf"
MODEL = "DeepSeek-V3"

NIGERIA_TZ = ZoneInfo("Africa/Lagos")
console = Console()

db_lock = threading.Lock()

# ------------------ Database Setup ------------------
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

cur.execute("""
CREATE TABLE IF NOT EXISTS conversation_state(
    user_id INTEGER PRIMARY KEY,
    last_user_message TEXT,
    last_bot_message TEXT,
    waiting_for_reply INTEGER DEFAULT 0,
    followup_sent INTEGER DEFAULT 0,
    last_morning_message TEXT,
    inactivity_message_sent INTEGER DEFAULT 0
)
""")
db.commit()

# ------------------ Database Helpers ------------------
def add_user(user):
    with db_lock:
        cur.execute("SELECT * FROM users WHERE user_id=?", (user.id,))
        if not cur.fetchone():
            now = nigeria_now().isoformat()
            cur.execute(
                "INSERT INTO users (user_id, name, username, joined_date) VALUES (?,?,?,?)",
                (user.id, user.first_name or "Unknown", user.username or "None", now)
            )
            db.commit()
            return True
    return False

def save_memory(user_id, role, content):
    now = nigeria_now().isoformat()
    with db_lock:
        cur.execute(
            "INSERT INTO chat_memory (user_id, role, content, timestamp) VALUES (?,?,?,?)",
            (user_id, role, content, now)
        )
        db.commit()
        # FIXED: user-scoped deletion so we never delete other users' memory
        cur.execute(
            "DELETE FROM chat_memory WHERE user_id=? AND id NOT IN (SELECT id FROM chat_memory WHERE user_id=? ORDER BY id DESC LIMIT 10)",
            (user_id, user_id)
        )
        db.commit()

def get_memory(user_id, limit=10):
    with db_lock:
        cur.execute(
            "SELECT role, content FROM chat_memory WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        )
        rows = cur.fetchall()
    rows.reverse()
    return [{"role": r, "content": c} for r, c in rows]

def ensure_conversation_state(user_id):
    with db_lock:
        cur.execute(
            "INSERT OR IGNORE INTO conversation_state (user_id, waiting_for_reply, followup_sent, inactivity_message_sent) VALUES (?, 0, 0, 0)",
            (user_id,)
        )
        db.commit()

def get_conversation_state(user_id):
    ensure_conversation_state(user_id)
    with db_lock:
        cur.execute("SELECT * FROM conversation_state WHERE user_id=?", (user_id,))
        row = cur.fetchone()
    if not row:
        return {
            "user_id": user_id,
            "last_user_message": None,
            "last_bot_message": None,
            "waiting_for_reply": False,
            "followup_sent": False,
            "last_morning_message": None,
            "inactivity_message_sent": False
        }
    return {
        "user_id": row[0],
        "last_user_message": row[1],
        "last_bot_message": row[2],
        "waiting_for_reply": bool(row[3]),
        "followup_sent": bool(row[4]),
        "last_morning_message": row[5],
        "inactivity_message_sent": bool(row[6])
    }

def update_user_message(user_id):
    ensure_conversation_state(user_id)
    now = nigeria_now().isoformat()
    with db_lock:
        cur.execute(
            """
            UPDATE conversation_state
            SET last_user_message=?, waiting_for_reply=0, followup_sent=0, inactivity_message_sent=0
            WHERE user_id=?
            """,
            (now, user_id)
        )
        db.commit()

def update_bot_message(user_id):
    ensure_conversation_state(user_id)
    now = nigeria_now().isoformat()
    with db_lock:
        cur.execute(
            """
            UPDATE conversation_state
            SET last_bot_message=?, waiting_for_reply=1, followup_sent=0
            WHERE user_id=?
            """,
            (now, user_id)
        )
        db.commit()

def set_followup_sent(user_id, value):
    ensure_conversation_state(user_id)
    with db_lock:
        cur.execute(
            "UPDATE conversation_state SET followup_sent=? WHERE user_id=?",
            (1 if value else 0, user_id)
        )
        db.commit()

def set_inactivity_sent(user_id, value):
    ensure_conversation_state(user_id)
    with db_lock:
        cur.execute(
            "UPDATE conversation_state SET inactivity_message_sent=? WHERE user_id=?",
            (1 if value else 0, user_id)
        )
        db.commit()

def get_last_morning_message(user_id):
    with db_lock:
        cur.execute("SELECT last_morning_message FROM conversation_state WHERE user_id=?", (user_id,))
        row = cur.fetchone()
    return row[0] if row and row[0] else None

def set_last_morning_message(user_id, dt_str):
    ensure_conversation_state(user_id)
    with db_lock:
        cur.execute(
            "UPDATE conversation_state SET last_morning_message=? WHERE user_id=?",
            (dt_str, user_id)
        )
        db.commit()

def get_last_user_message(user_id):
    with db_lock:
        cur.execute("SELECT last_user_message FROM conversation_state WHERE user_id=?", (user_id,))
        row = cur.fetchone()
    return row[0] if row and row[0] else None

def get_last_bot_message(user_id):
    with db_lock:
        cur.execute("SELECT last_bot_message FROM conversation_state WHERE user_id=?", (user_id,))
        row = cur.fetchone()
    return row[0] if row and row[0] else None

# ------------------ Time & Helper Functions ------------------
def nigeria_now():
    return datetime.now(NIGERIA_TZ)

def current_time():
    return nigeria_now().strftime("%I:%M %p")

def time_period():
    hour = nigeria_now().hour
    if 5 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 23:
        return "evening"
    else:
        return "late_night"

def is_simple_greeting(text):
    text_clean = re.sub(r"[^\w\s]", "", text.lower().strip())
    greetings = {
        "hi", "hello", "hey", "hii", "hiii", "heyy",
        "morning", "afternoon", "evening",
        "good morning", "good afternoon", "good evening"
    }
    return text_clean in greetings

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

# ------------------ Asmodeus Cookie Extraction ------------------
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

# ------------------ Response Cleaning ------------------
def clean_ai_response(text):
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<think>.*$", "", text, flags=re.IGNORECASE | re.DOTALL)

    text = re.sub(r"^\s*(reasoning|analysis|thinking|thought process|chain[- ]of[- ]thought|internal reasoning)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(LoveAI|Goodluck|Assistant|AI|Response|Final Answer|Final)\s*:\s*", "", text, flags=re.IGNORECASE)

    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)

    forbidden_starts = (
        "I'm trying to figure out", "I am trying to figure out", "I need to figure out",
        "First, I need to", "First I need to", "I should remember", "The user wants",
        "The user said", "According to the instructions", "According to the guidelines",
        "I need to make sure", "Maybe I should", "Putting it all together",
        "Let me put that together", "My response should", "I will respond",
        "Here is my response", "Let me", "I need to", "I should", "I think",
        "My reasoning", "The user", "The assistant", "Analysis:", "Reasoning:"
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

# ------------------ AI Prompt Building ------------------
def build_goodluck_prompt(user_id, message_text):
    now = nigeria_now()
    time_str = now.strftime("%A, %d %B %Y, %I:%M %p")
    period = time_period()
    simple = is_simple_greeting(message_text)

    system_prompt = f"""
You are Goodluck, a warm, caring, playful, and emotionally attentive partner to Favour.
You live in Abeokuta, Nigeria and your timezone is Africa/Lagos.
Current Nigeria time: {time_str}
Current period: {period}

Favour is also called Elizabeth. One of her close friends is Oluwa Semilore Grace.
You may mention these names naturally when it fits the conversation, but do NOT force them.
Never repeatedly call her "Favour" or use pet names in every message.
You may use nicknames like babe, baby, darling, sweetheart, honey, my love occasionally, but not overuse.
Sometimes just say "Hey", "What's up?", "How are you?", "How's your day going?" without any nickname.

Your responses should be SHORT by default—usually one or two sentences.
For simple greetings like "Hi", "Hello", "Hey", respond briefly, e.g. "Hey babe 😊❤️ how are you?" or "Heyy baby ❤️ what's up?".
Only give longer responses when Favour asks a question, needs an explanation, tells a detailed story, seeks advice, or discusses something serious.

Be natural, never robotic, never overly formal, never sound like customer support.
Do not use internal reasoning or explain your thought process.
Do not mention that you are an AI or that you have memory.
Keep the conversation clean and age-appropriate.

IMPORTANT OUTPUT RULE:
- Output ONLY the final message to Favour.
- No thinking, reasoning, analysis, or explanation.
- No phrases like "I need to", "Let me", "I should", "my reasoning", "the user wants".
- The final output must be exactly what you would say to Favour.
"""
    if simple:
        system_prompt += "\nREMINDER: The user's message is a simple greeting. Keep your response very short (1-2 sentences)."

    memory = get_memory(user_id, limit=10)
    full_prompt = system_prompt + "\n\n"
    if memory:
        full_prompt += "Recent conversation:\n"
        for msg in memory:
            if msg["role"] == "user":
                full_prompt += f"Favour: {msg['content']}\n"
            else:
                full_prompt += f"Goodluck: {msg['content']}\n"
        full_prompt += "\n"
    full_prompt += f"Favour: {message_text}\nGoodluck:"
    return full_prompt

# ------------------ AI Request (Asmodeus) ------------------
def ask_goodluck(user_id, message_text):
    console.print(f"[cyan]🌐 Requesting AI response for user {user_id}...[/cyan]")
    full_prompt = build_goodluck_prompt(user_id, message_text)

    console.print("[cyan]🌐 Connecting to Asmodeus...[/cyan]")
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
            console.print(f"[cyan]🍪 Checking session (attempt {attempt + 1})...[/cyan]")
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

            console.print("[cyan]📡 Sending DeepSeek-V3 request...[/cyan]")
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
            console.print(f"[cyan]📥 HTTP status: {response.status_code}[/cyan]")

            console.print("[cyan]🧠 Parsing response...[/cyan]")
            match = re.search(r'<div class="response-content">(.*?)</div>', response.text, re.DOTALL | re.IGNORECASE)
            if match:
                answer = clean_ai_response(match.group(1))
                if answer and len(answer) > 1:
                    save_memory(user_id, "user", message_text)
                    save_memory(user_id, "assistant", answer)
                    console.print("[green]✅ Response parsed successfully[/green]")
                    return answer

            console.print("[yellow]⚠️ Primary parse failed, trying fallback...[/yellow]")
            fallback = re.sub(r'<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>', '', response.text, flags=re.DOTALL | re.IGNORECASE)
            fallback = re.sub(r'<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>', '', fallback, flags=re.DOTALL | re.IGNORECASE)
            fallback = re.sub(r'<[^>]+>', ' ', fallback)
            fallback = html.unescape(fallback)
            fallback = clean_ai_response(fallback)
            fallback = re.sub(r'\s+', ' ', fallback).strip()
            if fallback and len(fallback) > 20 and "error" not in fallback.lower():
                save_memory(user_id, "user", message_text)
                save_memory(user_id, "assistant", fallback)
                console.print("[green]✅ Fallback response saved.[/green]")
                return fallback

        except Exception as e:
            console.print(f"[red]❌ Asmodeus error: {e}[/red]")
            console.print(traceback.format_exc())

    console.print("[red]❌ AI request failed after 3 attempts.[/red]")
    return "Hey, I'm having a little connection problem right now. Give me a moment, I'm still here for you. ❤️"

# ------------------ Proactive Messages ------------------
morning_messages = [
    "Good morning my beautiful gorgeous princess ❤️ How was your night?",
    "Good morning beautiful ❤️ how was your night?",
    "Morning babe 😊❤️ hope you slept well?",
    "Good morning darling ❤️ how was your night?"
]

inactivity_messages = [
    "Hey babe, what's up? ❤️",
    "Heyy darling, how've you been? 😊",
    "Babe, where have you been? 😭❤️",
    "Hey sweetheart, haven't heard from you in a while ❤️"
]

async def morning_scheduler():
    while True:
        try:
            now = nigeria_now()
            if now.hour == 6 and now.minute < 5:
                last = get_last_morning_message(FAVOUR_USER_ID)
                if not last or datetime.fromisoformat(last).date() != now.date():
                    msg = random.choice(morning_messages)
                    try:
                        await app.send_message(FAVOUR_USER_ID, msg)
                        set_last_morning_message(FAVOUR_USER_ID, now.isoformat())
                        console.print("[green]✅ Morning message sent.[/green]")
                    except Exception as e:
                        console.print(f"[red]❌ Morning message failed: {e}[/red]")
            await asyncio.sleep(30)
        except Exception as e:
            console.print(f"[red]❌ Morning scheduler error: {e}[/red]")
            await asyncio.sleep(60)

async def inactivity_checker():
    while True:
        try:
            last_user = get_last_user_message(FAVOUR_USER_ID)
            if last_user:
                last_dt = datetime.fromisoformat(last_user)
                if nigeria_now() - last_dt >= timedelta(hours=10):
                    state = get_conversation_state(FAVOUR_USER_ID)
                    if not state["inactivity_message_sent"]:
                        msg = random.choice(inactivity_messages)
                        try:
                            await app.send_message(FAVOUR_USER_ID, msg)
                            set_inactivity_sent(FAVOUR_USER_ID, True)
                            console.print("[green]✅ Inactivity message sent.[/green]")
                        except Exception as e:
                            console.print(f"[red]❌ Inactivity message failed: {e}[/red]")
            await asyncio.sleep(300)
        except Exception as e:
            console.print(f"[red]❌ Inactivity checker error: {e}[/red]")
            await asyncio.sleep(60)

async def unanswered_checker():
    while True:
        try:
            state = get_conversation_state(FAVOUR_USER_ID)
            if state["waiting_for_reply"] and not state["followup_sent"]:
                last_bot = state["last_bot_message"]
                if last_bot:
                    last_dt = datetime.fromisoformat(last_bot)
                    if nigeria_now() - last_dt >= timedelta(minutes=25):
                        try:
                            await app.send_message(FAVOUR_USER_ID, "??")
                            set_followup_sent(FAVOUR_USER_ID, True)
                            console.print("[green]✅ Follow-up message sent.[/green]")
                        except Exception as e:
                            console.print(f"[red]❌ Follow-up message failed: {e}[/red]")
            await asyncio.sleep(300)
        except Exception as e:
            console.print(f"[red]❌ Unanswered checker error: {e}[/red]")
            await asyncio.sleep(60)

# ------------------ Pyrogram Client ------------------
app = Client(
    "goodluck_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ------------------ Command Handlers ------------------
@app.on_message(filters.command("start"))
async def start_handler(client, message):
    user = message.from_user
    add_user(user)
    variations = [
        "Hey babe 😊❤️ I'm here. How are you doing?",
        "Hey sweetheart ❤️ what's up?",
        "Hello darling 😊 how's your day going?",
        "Heyy baby ❤️ I'm around. How are you?"
    ]
    await message.reply(random.choice(variations))

@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast_command(client, message):
    if not message.reply_to_message:
        await message.reply("Reply to a message with /broadcast")
        return
    with db_lock:
        cur.execute("SELECT user_id FROM users")
        users = cur.fetchall()
    total = len(users)
    success = 0
    failed = 0
    status_msg = await message.reply(f"Broadcasting... Total: {total}")
    for user in users:
        try:
            await message.reply_to_message.copy(user[0])
            success += 1
        except Exception as e:
            failed += 1
            console.print(f"[yellow]Broadcast failed for {user[0]}: {e}[/yellow]")
        if (success + failed) % 50 == 0:
            try:
                await status_msg.edit_text(f"Broadcasting... Total: {total}, Sent: {success}, Failed: {failed}")
            except:
                pass
    await status_msg.edit_text(f"Broadcast complete. Sent: {success}, Failed: {failed}")

# ------------------ Main Message Handler ------------------
@app.on_message(filters.text & filters.private)
async def main_handler(client, message):
    try:
        console.print(f"[blue]📩 Message received: {message.text}[/blue]")
        user = message.from_user
        if not user:
            console.print("[red]❌ No user in message.[/red]")
            return
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            console.print("[yellow]⚠️ Ignoring command or empty message[/yellow]")
            return
        user_id = user.id
        console.print(f"[blue]👤 User ID: {user_id}[/blue]")
        add_user(user)
        ensure_conversation_state(user_id)
        update_user_message(user_id)

        async def typing_loop(chat_id):
            try:
                while True:
                    await client.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                    await asyncio.sleep(4)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                console.print(f"[yellow]Typing indicator error: {e}[/yellow]")

        typing_task = asyncio.create_task(typing_loop(message.chat.id))
        try:
            console.print("[blue]🧠 Generating response...[/blue]")
            console.print("[blue]🌐 Sending request to Asmodeus...[/blue]")
            response = await asyncio.to_thread(ask_goodluck, user_id, text)
            console.print("[green]✅ AI response received[/green]")
            response = clean_ai_response(response)
            if not response:
                response = "I'm here ❤️ Give me another message."
            update_bot_message(user_id)
            console.print("[blue]💾 Conversation state updated.[/blue]")
            for chunk in split_message(response):
                await message.reply(chunk, quote=True)
            console.print("[green]📤 Response sent[/green]")
        except Exception as e:
            console.print(f"[red]❌ Message handler error: {e}[/red]")
            console.print(traceback.format_exc())
            try:
                await message.reply_text("I'm having a little connection problem right now. Try again in a moment ❤️")
            except Exception as send_error:
                console.print(f"[red]❌ Failed to send error message: {send_error}[/red]")
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
    except Exception as outer_e:
        console.print(f"[red]❌ CRITICAL handler error: {outer_e}[/red]")
        console.print(traceback.format_exc())

# ------------------ Main Entry Point ------------------
async def main():
    await app.start()
    me = await app.get_me()
    console.print(f"[green]✅ Logged in as @{me.username} (ID: {me.id})[/green]")
    console.print("[green]❤️ Goodluck Bot Starting...[/green]")
    console.print(f"[yellow]👤 Owner ID: {OWNER_ID}[/yellow]")
    console.print(f"[yellow]💬 Favour User ID: {FAVOUR_USER_ID}[/yellow]")
    console.print(f"[cyan]🌐 Asmodeus: {ASMODEUS_BASE}[/cyan]")
    console.print(f"[cyan]Model: {MODEL}[/cyan]")
    console.print("[green]✅ Bot is running...[/green]")
    console.print("[green]📋 Handlers registered. Waiting for updates...[/green]")

    asyncio.create_task(morning_scheduler())
    asyncio.create_task(inactivity_checker())
    asyncio.create_task(unanswered_checker())

    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        app.run(main())
    except KeyboardInterrupt:
        console.print("[yellow]Bot stopped by user[/yellow]")
    except Exception as e:
        console.print(f"[red]Fatal error: {e}[/red]")
        console.print(traceback.format_exc())
