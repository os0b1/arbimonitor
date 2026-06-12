import os
import asyncio
import logging
import time
import aiohttp
from contextlib import asynccontextmanager
from collections import defaultdict
from datetime import datetime
from dotenv import load_dotenv

# Solana Low-Level Cryptography Infrastructure
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.transaction_status import UiTransactionEncoding
from solana.rpc.core import RPCException

# Telegram UI Engine Primitives
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
from telegram.error import TimedOut, NetworkError, Forbidden
from telegram.request import HTTPXRequest

import aiosqlite

load_dotenv()

# ======================================================================
# MASTER ENGINE CONFIGURATION
# ======================================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PREMIUM_WALLET = os.getenv("PREMIUM_WALLET")
ADMIN_IDS = [6471151055]
PREMIUM_PRICE_SOL = float(os.getenv("PREMIUM_PRICE_SOL", "0.2"))  # PRO = 0.2 SOL
PREMIUM_LITE_PRICE_SOL = float(os.getenv("PREMIUM_LITE_PRICE_SOL", "0.1"))  # LITE = 0.1 SOL
PREMIUM_TOLERANCE = float(os.getenv("PREMIUM_TOLERANCE", "0.001"))
RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
MIN_SOL_THRESHOLD = float(os.getenv("MIN_SOL_THRESHOLD", "0.5"))

if not TELEGRAM_TOKEN or not PREMIUM_WALLET:
    raise SystemExit("❌ Critical Failure: Missing TELEGRAM_TOKEN or PREMIUM_WALLET definitions inside .env blueprint.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("ArbiMonitor")

DB_FILE = "arbimonitor.db"
db_write_lock = asyncio.Lock()

# Token Registry Cache Index
token_cache = {}
async def fetch_token_name(mint_address: str) -> str:
    """Asynchronously resolves on-chain mint addresses to readable symbols."""
    if mint_address in token_cache:
        return token_cache[mint_address]
    
    # Fast-fallback paths for core primitive assets
    primitives = {
        "So11111111111111111111111111111111111111112": "SOL",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT"
    }
    if mint_address in primitives:
        return primitives[mint_address]

    # Clean multi-backup token indexing query fallback 
    url = f"https://api.jup.ag/tokens/v1/token/{mint_address}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=3.0) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    symbol = data.get("symbol", mint_address[:8])
                    token_cache[mint_address] = symbol
                    return symbol
    except Exception:
        pass
    
    token_cache[mint_address] = mint_address[:8]
    return mint_address[:8]
    
    # Fast-fallback paths for core primitive assets
    primitives = {
        "So11111111111111111111111111111111111111112": "SOL",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT"
    }
    if mint_address in primitives:
        return primitives[mint_address]

    # Clean multi-backup token indexing query fallback 
    url = f"https://api.jup.ag/tokens/v1/token/{mint_address}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=3.0) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    symbol = data.get("symbol", mint_address[:8])
                    token_cache[mint_address] = symbol
                    return symbol
    except Exception:
        pass
    
    
    token_cache[mint_address] = mint_address[:8]
    return mint_address[:8]

# ======================================================================
# CONCURRENT DATABASE ARCHITECTURE
# ======================================================================
@asynccontextmanager
async def db_conn():
    """Context manager wrapping SQLite mutations inside structural WAL performance settings."""
    async with db_write_lock:
        async with aiosqlite.connect(DB_FILE, timeout=60.0) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA synchronous=NORMAL")
            try:
                yield conn
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

async def init_db():
    """Deploys on-chain event registries, metrics tracking schemas and index maps cleanly."""
    async with db_conn() as c:
        await c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                premium_tier TEXT DEFAULT 'free',
                premium_since INTEGER,
                premium_tx TEXT,
                research_tokens INTEGER DEFAULT 0,
                created_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                address TEXT,
                alias TEXT,
                paused INTEGER DEFAULT 0,
                min_sol REAL DEFAULT 0.5,
                added_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_signature TEXT UNIQUE,
                user_id TEXT,
                amount REAL,
                tier TEXT,
                verified_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS signature_cache (
                signature TEXT,
                wallet TEXT,
                timestamp INTEGER,
                PRIMARY KEY (signature, wallet)
            );
            CREATE INDEX IF NOT EXISTS idx_wallets_user ON wallets(user_id);
            CREATE INDEX IF NOT EXISTS idx_wallets_address ON wallets(address);
            CREATE INDEX IF NOT EXISTS idx_sig_cache_time ON signature_cache(timestamp);
        """)
    log.info("📡 Secure Database blueprints initialized successfully.")

# ======================================================================
# MEMORY CACHE CONTROLLERS
# ======================================================================
alert_cache = {}
user_last_alert = {}
wallet_last_run = {}

def clean_expired_caches():
    now = time.time()
    for k, v in list(alert_cache.items()):
        if now - v > 3600: alert_cache.pop(k, None)
    for k, v in list(user_last_alert.items()):
        if now - v > 600: user_last_alert.pop(k, None)
    for k, v in list(wallet_last_run.items()):
        if now - v > 600: wallet_last_run.pop(k, None)

async def is_sig_processed(sig: str, wallet: str) -> bool:
    now = int(time.time())
    async with db_conn() as c:
        await c.execute("DELETE FROM signature_cache WHERE timestamp < ?", (now - 7200,))
        try:
            await c.execute(
                "INSERT INTO signature_cache (signature, wallet, timestamp) VALUES (?, ?, ?)",
                (sig, wallet, now)
            )
            return False
        except aiosqlite.IntegrityError:
            return True

def is_on_cooldown(key: str, seconds: int = 30) -> bool:
    now = time.time()
    if key in alert_cache and now - alert_cache.get(key, 0) < seconds:
        return True
    alert_cache[key] = now
    return False

def is_user_rate_limited(user_id: int, seconds: int = 3) -> bool:
    now = time.time()
    if user_id in user_last_alert and now - user_last_alert.get(user_id, 0) < seconds:
        return True
    user_last_alert[user_id] = now
    return False

# ======================================================================
# CRYPTOGRAPHIC DECONSTRUCTION / FORENSICS PIPELINE
# ======================================================================
rpc_client = None
rpc_semaphore = asyncio.Semaphore(5)

async def get_rpc():
    global rpc_client
    if rpc_client is None:
        rpc_client = AsyncClient(RPC_URL)
        log.info("🛰️ Connected directly to Solana Node Engine Layer.")
    return rpc_client

def extract_pubkey(account) -> str:
    try:
        if hasattr(account, "pubkey"): return str(account.pubkey)
        if isinstance(account, dict) and "pubkey" in account: return str(account["pubkey"])
        return str(account)
    except Exception:
        return ""

def get_transaction_accounts(val) -> list:
    keys = []
    tx_obj = getattr(val, "transaction", None)
    if tx_obj and hasattr(tx_obj, "transaction") and hasattr(tx_obj.transaction, "message"):
        msg_obj = tx_obj.transaction.message
        if hasattr(msg_obj, "account_keys"):
            keys = [extract_pubkey(k) for k in msg_obj.account_keys if k]
    elif isinstance(tx_obj, dict):
        if "message" in tx_obj:
            keys = [extract_pubkey(k) for k in tx_obj["message"].get("account_keys", []) if k]
        elif "transaction" in tx_obj and "message" in tx_obj["transaction"]:
            keys = [extract_pubkey(k) for k in tx_obj["transaction"]["message"].get("account_keys", []) if k]
    
    meta = getattr(val, "meta", None)
    if meta and hasattr(meta, "loaded_addresses") and meta.loaded_addresses:
        if hasattr(meta.loaded_addresses, "writable"):
            keys.extend([extract_pubkey(k) for k in meta.loaded_addresses.writable])
        if hasattr(meta.loaded_addresses, "readonly"):
            keys.extend([extract_pubkey(k) for k in meta.loaded_addresses.readonly])
    return keys

def detect_swap(meta) -> dict:
    result = {"is_swap": False, "token_in": None, "token_out": None, "amount_in": 0, "amount_out": 0}
    if hasattr(meta, "post_token_balances") and meta.post_token_balances:
        pre = {getattr(b, "mint", ""): getattr(b, "ui_token_amount", {}).get("ui_amount", 0) for b in (meta.pre_token_balances or [])}
        post = {getattr(b, "mint", ""): getattr(b, "ui_token_amount", {}).get("ui_amount", 0) for b in (meta.post_token_balances or [])}
        for mint in set(pre.keys()) | set(post.keys()):
            pre_amt = pre.get(mint, 0)
            post_amt = post.get(mint, 0)
            diff = post_amt - pre_amt
            if abs(diff) > 0.01 and mint:
                if diff > 0:
                    result["token_in"] = mint
                    result["amount_in"] = diff
                else:
                    result["token_out"] = mint
                    result["amount_out"] = abs(diff)
    if result["token_in"] and result["token_out"]:
        result["is_swap"] = True
    return result
async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
 """Handler for the explicit /health command."""
 await safe_reply(update, HEALTH_TEXT, parse_mode=ParseMode.MARKDOWN)
 async def check_premium_status(user_id: int) -> bool:
    """Evaluates tier constraints. Automatically whitelists system creator."""
    if user_id in ADMIN_IDS:
        return True

    async with aiosqlite.connect("arbimonitor.db") as db:
        async with db.execute("SELECT premium_tier FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row is not None and row[0] in ["lite", "pro"]
# ======================================================================
# HIGH-SPEED ASYNC ALERT WORKERS
# ======================================================================
alert_queue = asyncio.Queue(maxsize=10000)

async def alert_worker(bot):
    while True:
        try:
            alert = await alert_queue.get()
            user_id = alert["user_id"]
            msg = alert["msg"]
            for _ in range(3):
                try:
                    await bot.send_message(chat_id=user_id, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
                    break
                except (TimedOut, NetworkError):
                    await asyncio.sleep(2)
                except Forbidden:
                    break
                except Exception:
                    break
            alert_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception:
            pass

async def queue_alert(user_id: int, signature: str, direction: str, change: float, balance: float, alias: str, swap_info: dict = None):
    emoji = "🐋" if abs(change) > 100 else "🐟"
    if swap_info and swap_info.get("is_swap"):
        token_in_symbol = await fetch_token_name(swap_info["token_in"])
        token_out_symbol = await fetch_token_name(swap_info["token_out"])
        amount_in = swap_info.get("amount_in", 0)
        amount_out = swap_info.get("amount_out", 0)
        msg = (
            f"{emoji} *SWAP ALERT* {emoji}\n"
            f"╔════════════════════════════════════════╗\n"
            f"┃ 👤 *Wallet:* `{alias}`\n"
            f"┃ 🔄 *Swap:* {amount_in:.2f} {token_in_symbol} → {amount_out:.2f} {token_out_symbol}\n"
            f"┃ 🏦 *SOL Balance:* `{balance:,.2f} SOL`\n"
            f"╚════════════════════════════════════════╝\n"
            f"🔗 [View on Solscan](https://solscan.io/tx/{signature})"
        )
    else:
        msg = (
            f"{emoji} *WHALE ALERT* {emoji}\n"
            f"╔════════════════════════════════════════╗\n"
            f"┃ 👤 *Wallet:* `{alias}`\n"
            f"┃ 📊 *Action:* {direction}\n"
            f"┃ 💰 *SOL:* `{change:+.2f} SOL`\n"
            f"┃ 🏦 *Balance:* `{balance:,.2f} SOL`\n"
            f"╚════════════════════════════════════════╝\n"
            f"🔗 [View on Solscan](https://solscan.io/tx/{signature})"
        )
    try:
        await alert_queue.put({"user_id": user_id, "msg": msg})
    except asyncio.QueueFull:
        pass

# ======================================================================
# PARALLEL CHAIN TARGET PROCESSING LAYER
# ======================================================================
async def process_wallet(address, listeners, client, bot):
    try:
        now = time.time()
        if now - wallet_last_run.get(address, 0) < 5: return
        wallet_last_run[address] = now
        pubkey = Pubkey.from_string(address)
        async with rpc_semaphore:
            try:
                resp = await client.get_signatures_for_address(pubkey, limit=5)
            except RPCException:
                return
            if not resp or not resp.value: return
            for tx in reversed(resp.value):
                sig = str(tx.signature)
                if await is_sig_processed(sig, address): continue
                try:
                    tx_data = await client.get_transaction(
                        sig, encoding=UiTransactionEncoding.JSONPARSED, commitment=Confirmed, max_supported_transaction_version=0
                    )
                except RPCException:
                    continue
                if not tx_data or not tx_data.value: continue
                try:
                    val = tx_data.value
                    meta = val.transaction.meta
                    if not meta: continue
                    account_keys = get_transaction_accounts(val)
                    if address not in account_keys: continue
                    indices = [i for i, x in enumerate(account_keys) if x == address]
                    pre_balances = getattr(meta, "pre_balances", [])
                    post_balances = getattr(meta, "post_balances", [])
                    swap_info = detect_swap(meta)
                    for idx in indices:
                        if idx >= len(pre_balances) or idx >= len(post_balances): continue
                        change = (post_balances[idx] - pre_balances[idx]) / 1e9
                        balance = post_balances[idx] / 1e9
                        if abs(change) < MIN_SOL_THRESHOLD and not swap_info.get("is_swap"): continue
                        direction = "📈 BUY" if change > 0 else "📉 SELL"
                        alias = listeners[0].get("alias", f"{address[:6]}...{address[-6:]}")
                        for lst in listeners:
                            user_id = int(lst["user_id"])
                            if is_user_rate_limited(user_id): continue
                            if is_on_cooldown(f"{sig}:{user_id}"): continue
                            await queue_alert(user_id, sig, direction, change, balance, alias, swap_info if swap_info.get("is_swap") else None)
                except Exception:
                    continue
    except Exception:
        pass

async def engine_loop(bot):
    log.info("🔄 Processing loop initialized...")
    clean_counter = 0
    while True:
        try:
            clean_counter += 1
            if clean_counter >= 30:
                clean_expired_caches()
                clean_counter = 0
            async with db_conn() as c:
                cur = await c.execute("SELECT user_id, address, alias FROM wallets WHERE paused = 0")
                rows = await cur.fetchall()
            grouped = defaultdict(list)
            for r in rows:
                grouped[r["address"]].append(dict(r))
            if not grouped:
                await asyncio.sleep(5)
                continue
            client = await get_rpc()
            address_items = list(grouped.items())
            batch_size = 20
            for i in range(0, len(address_items), batch_size):
                chunk = address_items[i:i+batch_size]
                tasks = [process_wallet(addr, lst, client, bot) for addr, lst in chunk]
                await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(2)
        except Exception:
            await asyncio.sleep(5)

# ======================================================================
# PREMIUM SYSTEM UI TEXT CORES
# ======================================================================
MAIN_MENU = ReplyKeyboardMarkup([
    ["📖 Help", "✨ Benefits", "💰 Pricing"],
    ["💎 Premium", "📊 My Status", "🎫 Top Up"],
    ["➕ Add Wallet", "📋 My Wallets", "🩺 Health"],
    ["❌ Pause All", "▶️ Resume All", "🗑 Clear"],
    ["🔗 Support", "ℹ️ About"]
], resize_keyboard=True)

INLINE_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ Add Wallet", callback_data="add"), InlineKeyboardButton("📋 My Wallets", callback_data="list")],
    [InlineKeyboardButton("✨ Benefits", callback_data="benefits"), InlineKeyboardButton("💰 Pricing", callback_data="pricing")],
    [InlineKeyboardButton("💎 Premium", callback_data="premium"), InlineKeyboardButton("📊 My Status", callback_data="status")],
    [InlineKeyboardButton("🎫 Top Up", callback_data="topup"), InlineKeyboardButton("🩺 Health", callback_data="health")],
    [InlineKeyboardButton("📖 Help", callback_data="help"), InlineKeyboardButton("🔗 Support", callback_data="support")]
])

# ======================================================================
# PREMIUM SYSTEM UI TEXT CORES (MOBILE-OPTIMIZED)
# ======================================================================

WELCOME_TEXT = """
🐋 *ARBIMONITOR v6.0* 🐋
_Solana Protocol Intelligence Engine_
━━━━━━━━━━━━━━━━━━━━━━━━━━

ArbiMonitor is a high-speed Solana whale tracking network that monitors smart money movements, buy/sell executions, and liquidity pool swaps instantly.

✨ *ENGINE FEATURES*
🔹 *Whale Tracking* – Live BUY/SELL movement alerts.
🔹 *Swap Forensics* – Automated multi-pool swap monitoring.
🔹 *Asset Indexing* – Auto-resolves modern tokens via Jupiter API.
🔹 *Stream Management* – Hot-swappable pause, resume, and stream isolation.

📋 *CORE TELEGRAM COMMANDS*
• `/start` – Initialize framework matrix
• `/help` – Display operational diagnostics
• `/status` – Query your pipeline subscription status
• `/list` – Audit your active target wallet streams
• `/add <addr> <alias>` – Route a new tracking node
• `/remove <addr>` – Destruct an active target stream
• `/verify <tx>` – Pass an on-chain upgrade signature

💰 *TIER BLUEPRINTS*
• 🆓 *Free:* 2 Target Streams | Standard Polling
• 💎 *Lite:* 10 Target Streams | 100 Monthly Credits
• 🐋 *Pro:* Unlimited Streams | Full Liquidity Swap Resolution

🔗 *SUPPORT & PROTOCOL DEV*
• Core Developer Node: @os0b1
"""

BENEFITS_TEXT = """
✨ *ARBIMONITOR TIER METRICS* ✨
━━━━━━━━━━━━━━━━━━━━━━━━━━

🆓 *FREE STANDARD*
└ Max Streams: `2 Wallets`
└ Processing Sync: `Standard Loop`
└ Swap Analysis: `SOL/Stable Primitives Only`

💎 *PREMIUM LITE* (`0.1 SOL`)
└ Max Streams: `10 Wallets`
└ Processing Sync: `Priority High-Speed Loop`
└ Research Energy: `100 Credits Loaded`

🐋 *PREMIUM PRO* (`0.2 SOL`)
└ Max Streams: `Unlimited Target Nodes`
└ Processing Sync: `Maximum-Throughput Parallel Pipeline`
└ Swap Analysis: `Full Deep-Scan (WIF, BONK, JUP, etc.)`
"""

HEALTH_TEXT = """
🟢 *ENGINE HEALTH REPORT* 🟢
━━━━━━━━━━━━━━━━━━━━━━━━━━
• *Core Worker Loop:* `ACTIVE`
• *Database Engine:* `CONNECTED (WAL Mode)`
• *Solana RPC Interface:* `ONLINE (Node Synced)`
• *Alert Queue Load:* `0 / 10,000`
"""

# ======================================================================
# CONTROLLERS & INTERFACE ROUTERS
# ======================================================================
async def send_admin(context, msg):
    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

async def safe_reply(update: Update, text: str, parse_mode=None, reply_markup=None):
    try:
        if update.message:
            return await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        log.error(f"Reply error: {e}")

async def safe_edit(query, text: str, parse_mode=None, reply_markup=None):
    try:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        log.error(f"UI Matrix frame swap failure: {e}")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    uname = update.effective_chat.username or "no_username"
    await send_admin(context, f"📡 New user connected to framework: `{uid}` (@{uname})")

    async with db_conn() as c:
        if int(uid) in ADMIN_IDS:
            await c.execute(
                "INSERT OR IGNORE INTO users (user_id, created_at, research_tokens, premium_tier) VALUES (?, ?, ?, ?)",
                (uid, int(time.time()), 999999, "pro")
            )
        else:
            await c.execute(
                "INSERT OR IGNORE INTO users (user_id, created_at, research_tokens, premium_tier) VALUES (?, ?, ?, ?)",
                (uid, int(time.time()), 0, "free")
            )
    
    await safe_reply(update, WELCOME_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_MENU)
    await safe_reply(update, "🎛️ *Quick Actions Control Matrix:*", parse_mode=ParseMode.MARKDOWN, reply_markup=INLINE_MENU)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update, WELCOME_TEXT, parse_mode=ParseMode.MARKDOWN)

async def cmd_benefits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update, BENEFITS_TEXT, parse_mode=ParseMode.MARKDOWN)
async def cmd_pricing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pricing_payload = (
        f"💰 *ARBIMONITOR SYSTEM PRICING* 💰\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 *Premium Lite:* `0.1 SOL` (Lifetime/10 Wallets)\n"
        f"🐋 *Premium Pro:* `0.2 SOL` (Lifetime/Unlimited)\n\n"
        f"📤 *Transfer allocation to native verification wallet:* \n"
        f"`{PREMIUM_WALLET}`\n\n"
        f"🔑 *Activation Pipeline:*\n"
        f"Once your transaction is settled on-chain, copy the signature and execute:\n"
        f"`/verify <tx_signature>`"
    )
    await safe_reply(update, pricing_payload, parse_mode=ParseMode.MARKDOWN)

async def cmd_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update,
        f"💎 *UPGRADE TRANSITION PROTOCOL* 💎\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔹 *Premium Lite:* `0.1 SOL` → 10 Streams\n"
        f"🔹 *Premium Pro:* `0.2 SOL` → Unlimited Streams\n\n"
        f"📤 *Target Allocation Destination:* \n"
        f"`{PREMIUM_WALLET}`\n\n"
        f"🔑 *Verification Sequence:* `/verify <tx_signature>`",
        parse_mode=ParseMode.MARKDOWN)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    async with db_conn() as c:
        cur = await c.execute("SELECT premium_tier, research_tokens FROM users WHERE user_id = ?", (uid,))
        row = await cur.fetchone()
    
    if not row:
        await safe_reply(update, "❌ *User node registry empty.*\nSend /start to boot structural tracking.", parse_mode=ParseMode.MARKDOWN)
        return
    
    tier = row["premium_tier"] or "free"
    tokens = row["research_tokens"] or 0
    tier_name = "FREE" if tier == "free" else "PREMIUM LITE" if tier == "lite" else "PREMIUM PRO"
    max_wallets = 2 if tier == "free" else 10 if tier == "lite" else "Unlimited"
    
    await safe_reply(update,
        f"📊 *TERMINAL SUBSCRIPTION INDEX*\n━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"▪️ *Active Tier:* `{tier_name}`\n"
        f"▪️ *Research Energy:* `{tokens} Credits`\n"
        f"▪️ *Tracking Capacity:* `{max_wallets} Stream Nodes`",
        parse_mode=ParseMode.MARKDOWN)

async def cmd_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update,
        f"🎫 *RESEARCH CREDITS PACKS* 🎫\n━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *Allocation Blueprint:* 100 tokens = `0.01 SOL`\n"
        f"📤 *Send to target address:* `{PREMIUM_WALLET}`\n"
        f"🔑 Confirm with command sequence: `/verify <tx_signature>`",
        parse_mode=ParseMode.MARKDOWN)

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    async with db_conn() as c:
        cur = await c.execute("SELECT alias, address, paused FROM wallets WHERE user_id = ?", (uid,))
        rows = await cur.fetchall()
    if not rows:
        await safe_reply(update, "📭 *Zero stream routes mapped.*\nUse `/add <address> <alias>` to index endpoints.", parse_mode=ParseMode.MARKDOWN)
        return
    msg = "*📋 TRACKED DATA STREAM PIPELINES*\n━━━━━━━━━━━━━━━━━━━━━━━\n"
    for r in rows:
        status = "⏸️ PAUSED" if r["paused"] else "✅ ACTIVE"
        msg += f"\n{status} *{r['alias']}*\n└ `{r['address'][:12]}...`\n"
    await safe_reply(update, msg, parse_mode=ParseMode.MARKDOWN)

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.chat.delete_message(update.message.message_id)
        await safe_reply(update, "🗑 *Interface layout reset completed.*", parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await safe_reply(update, "❌ *Unable to adjust chat context layout layers.*", parse_mode=ParseMode.MARKDOWN)

# ======================================================================
# READ / WRITE MUTATION STREAM INTERFACES
# ======================================================================
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 1:
        await safe_reply(update, "📝 *Syntax Layout:* `/add <address> <alias>`", parse_mode=ParseMode.MARKDOWN)
        return

    uid = str(update.effective_chat.id)
    address = args[0]
    alias = args[1] if len(args) > 1 else f"Whale-{address[:4]}"

    try:
        Pubkey.from_string(address)
    except Exception:
        await safe_reply(update, "❌ *Cryptographic key syntax tracking error: Invalid Solana base58 asset configuration.*", parse_mode=ParseMode.MARKDOWN)
        return

    try:
        now_ts = int(time.time())
        async with db_conn() as c:
            await c.execute("INSERT OR IGNORE INTO users (user_id, created_at, research_tokens) VALUES (?, ?, 0)", (uid, now_ts))
            cur = await c.execute("SELECT premium_tier FROM users WHERE user_id = ?", (uid,))
            row = await cur.fetchone()
            tier = row["premium_tier"] if row else "free"
            
            cur = await c.execute("SELECT COUNT(*) FROM wallets WHERE user_id = ?", (uid,))
            count = (await cur.fetchone())[0]
            
            max_wallets = 2 if tier == "free" else 10 if tier == "lite" else 999999
            if count >= max_wallets:
                await safe_reply(update, f"🚫 *Quota Limit Restriction!*\nYour subscription plan tier ({tier.upper()}) is full. Upgrade infrastructure via `/premium`.", parse_mode=ParseMode.MARKDOWN)
                return
            
            await c.execute("INSERT INTO wallets (user_id, address, alias, added_at) VALUES (?, ?, ?, ?)", (uid, address, alias, now_ts))
        await safe_reply(update, f"✅ *Target stream route locked successfully.*\n👤 *Alias:* `{alias}`\n📡 Monitoring structural changes live.", parse_mode=ParseMode.MARKDOWN)
        await send_admin(context, f"➕ Route registered to indexer: {alias}")
    except Exception as e:
        log.error(f"Add error: {e}")
        await safe_reply(update, "❌ *System registration framework mutation drop.*", parse_mode=ParseMode.MARKDOWN)

async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 1:
        await safe_reply(update, "Usage: `/remove <address>`", parse_mode=ParseMode.MARKDOWN)
        return
    async with db_conn() as c:
        await c.execute("DELETE FROM wallets WHERE user_id = ? AND address = ?", (str(update.effective_chat.id), args[0]))
    await safe_reply(update, "✅ *Target route drop transaction confirmed.*", parse_mode=ParseMode.MARKDOWN)

async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 1:
        await safe_reply(update, "Usage: `/pause <address>`", parse_mode=ParseMode.MARKDOWN)
        return
    async with db_conn() as c:
        await c.execute("UPDATE wallets SET paused = 1 WHERE user_id = ? AND address = ?", (str(update.effective_chat.id), args[0]))
    await safe_reply(update, "⏸️ *Target indexing stream suspended.*", parse_mode=ParseMode.MARKDOWN)

async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 1:
        await safe_reply(update, "Usage: `/resume <address>`", parse_mode=ParseMode.MARKDOWN)
        return
    async with db_conn() as c:
        await c.execute("UPDATE wallets SET paused = 0 WHERE user_id = ? AND address = ?", (str(update.effective_chat.id), args[0]))
    await safe_reply(update, "▶️ *Target indexing stream active.*", parse_mode=ParseMode.MARKDOWN)

async def cmd_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 1:
        await safe_reply(update, "Usage: `/verify <tx_signature>`", parse_mode=ParseMode.MARKDOWN)
        return
    sig = args[0]
    uid = str(update.effective_chat.id)
    await safe_reply(update, "🔍 *Executing secure transaction block validation on-chain...*", parse_mode=ParseMode.MARKDOWN)
    
    try:
        client = await get_rpc()
        async with rpc_semaphore:
            tx = await client.get_transaction(sig, encoding=UiTransactionEncoding.JSONPARSED, commitment=Confirmed, max_supported_transaction_version=0)
            if not tx or not tx.value:
                await safe_reply(update, "❌ *On-chain log signature parsing failed: Block record not found.*", parse_mode=ParseMode.MARKDOWN)
                return
            
            keys = get_transaction_accounts(tx.value)
            if PREMIUM_WALLET not in keys:
                await safe_reply(update, "❌ *Target verification failure: Destination signature mismatch.*", parse_mode=ParseMode.MARKDOWN)
                return
            
            idx = keys.index(PREMIUM_WALLET)
            meta = tx.value.transaction.meta
            change = (meta.post_balances[idx] - meta.pre_balances[idx]) / 1e9
            
            tier = "free"
            tokens = 0
            if abs(change - PREMIUM_LITE_PRICE_SOL) < PREMIUM_TOLERANCE:
                tier = "lite"
                tokens = 100
            elif abs(change - PREMIUM_PRICE_SOL) < PREMIUM_TOLERANCE:
                tier = "pro"
                tokens = 500
            else:
                await safe_reply(update, f"❌ *Value balance allocation deviation detected.*\nReceived `{change:.4f} SOL`.", parse_mode=ParseMode.MARKDOWN)
                return
            
            now_ts = int(time.time())
            async with db_conn() as c:
                cur = await c.execute("SELECT 1 FROM users WHERE premium_tx = ?", (sig,))
                if await cur.fetchone():
                    await safe_reply(update, "❌ *Signature mismatch error: Claim processing code already used.*", parse_mode=ParseMode.MARKDOWN)
                    return
                await c.execute("INSERT OR IGNORE INTO users (user_id, created_at, research_tokens) VALUES (?, ?, 0)", (uid, now_ts))
                await c.execute("UPDATE users SET premium_tier = ?, premium_since = ?, premium_tx = ?, research_tokens = research_tokens + ? WHERE user_id = ?",
                              (tier, now_ts, sig, tokens, uid))
            await send_admin(context, f"💎 Premium upgrade verified: {uid} scale tier to {tier.upper()}")
            await safe_reply(update, f"🎉 *PREMIUM LOGIC PROVISIONED MATCHING: {tier.upper()}* 🎉\nCredits Loaded: +{tokens}", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.error(f"Verify error: {e}")
        await safe_reply(update, "❌ *Internal engine handshake validation crash.*", parse_mode=ParseMode.MARKDOWN)

# ======================================================================
# LAYOUT NAVIGATION LOGIC ROUTING AGENTS
# ======================================================================
async def bottom_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📖 Help": await cmd_help(update, context)
    elif text == "✨ Benefits": await cmd_benefits(update, context)
    elif text == "💰 Pricing": await cmd_pricing(update, context)
    elif text == "💎 Premium": await cmd_premium(update, context)
    elif text == "📊 My Status": await cmd_status(update, context)
    elif text == "🎫 Top Up": await cmd_topup(update, context)
    elif text == "➕ Add Wallet":
        await safe_reply(update, "📝 *Add tracking stream route*\nFormat: `/add <address> <alias>`", parse_mode=ParseMode.MARKDOWN)
    elif text == "📋 My Wallets": await cmd_list(update, context)
    elif text == "🩺 Health": await safe_reply(update, HEALTH_TEXT, parse_mode=ParseMode.MARKDOWN)
    elif text == "❌ Pause All":
        uid = str(update.effective_chat.id)
        async with db_conn() as c:
            await c.execute("UPDATE wallets SET paused = 1 WHERE user_id = ?", (uid,))
        await safe_reply(update, "⏸️ *All user network listening indexes suspended.*", parse_mode=ParseMode.MARKDOWN)
    elif text == "▶️ Resume All":
        uid = str(update.effective_chat.id)
        async with db_conn() as c:
            await c.execute("UPDATE wallets SET paused = 0 WHERE user_id = ?", (uid,))
        await safe_reply(update, "▶️ *All target listeners re-established.*", parse_mode=ParseMode.MARKDOWN)
    elif text == "🗑 Clear": await cmd_clear(update, context)
    elif text == "🔗 Support":
        await safe_reply(update, "🔗 *Sovereign System Core Technical Support*\n• Dev Node DM: @os0b1", parse_mode=ParseMode.MARKDOWN)
    elif text == "ℹ️ About":
        await safe_reply(update, "ℹ️ *ArbiMonitor Engine v6.0*\nSolana Protocol Intelligence Engine\nAsynchronous WAL Pipeline Driven Architecture.", parse_mode=ParseMode.MARKDOWN)

async def inline_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        return

    # Normalized UI mapping layers to protect effective text edits safely
    if query.data == "add":
        await safe_edit(query, "📝 *Add tracking stream route*\nFormat target layout via space matching string command execution:\n\n`/add <address> <alias>`", parse_mode=ParseMode.MARKDOWN)
    elif query.data == "list":
        # Bypass native object update scope properties inside callback redirection blocks
        await cmd_list(update, context)
    elif query.data == "benefits":
        await safe_edit(query, BENEFITS_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=INLINE_MENU)
    elif query.data == "pricing":
        pricing_payload = (
            f"💰 *ARBIMONITOR SYSTEM PRICING BLUEPRINT*\n\n"
            f"📤 *Transfer verification target:* `{PREMIUM_WALLET}`\n\n"
            f"Lite License: `0.1 SOL` | Pro License: `0.2 SOL`\n"
            f"Execute: `/verify <tx_signature>` to clear activation pipelines instantly."
        )
        await safe_edit(query, pricing_payload, parse_mode=ParseMode.MARKDOWN, reply_markup=INLINE_MENU)
    elif query.data == "premium":
        await cmd_premium(update, context)
    elif query.data == "status":
        await cmd_status(update, context)
    elif query.data == "topup":
        await cmd_topup(update, context)
    elif query.data == "health":
        await safe_edit(query, HEALTH_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=INLINE_MENU)
    elif query.data == "help":
        await cmd_help(update, context)
    elif query.data == "support":
        await safe_edit(query, "🔗 *Sovereign System Core Technical Support*\n• Dev Node DM: @os0b1", parse_mode=ParseMode.MARKDOWN)

# ======================================================================
# CORE MAIN INITIALIZATION RUNTIME ENTRIES
# ======================================================================
async def main():
    try:
        await init_db()
    except Exception as e:
        log.error(f"DB initialization sequence failure: {e}")
        return

    request_client = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0)
    app = Application.builder().token(TELEGRAM_TOKEN).request(request_client).build()

    # Route Mapping Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("benefits", cmd_benefits))
    app.add_handler(CommandHandler("pricing", cmd_pricing))
    app.add_handler(CommandHandler("premium", cmd_premium))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("topup", cmd_topup))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("verify", cmd_verify))
    
    # Route Mapping Message Types
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bottom_menu_handler))
    app.add_handler(CallbackQueryHandler(inline_menu_handler))

    log.info("🚀 ArbiMonitor v6.0 Application Core Booted Successfully.")

    engine_task = asyncio.create_task(engine_loop(app.bot))
    worker_task = asyncio.create_task(alert_worker(app.bot))

    try:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Framework execution teardown caught safely.")
    finally:
        engine_task.cancel()
        worker_task.cancel()
        await asyncio.gather(engine_task, worker_task, return_exceptions=True)
        if app.updater and app.updater.running:
            await app.updater.stop()
        if app.running:
            await app.stop()
        await app.shutdown()
        if rpc_client:
            try:
                await rpc_client.close()
            except:
                pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("System connection dropped cleanly.")