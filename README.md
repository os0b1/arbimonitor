# 🐋 ArbiMonitor — Solana Protocol Intelligence Engine

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Telegram](https://img.shields.io/badge/Telegram-ArbiMonitor_Bot-blue.svg)](https://t.me/arbimonitor_bot)

**ArbiMonitor** is a production-grade, real-time Solana whale intelligence bot that delivers on-chain forensics and execution tracking directly to your Telegram terminal.

It doesn't just alert you. It classifies wallet behavior, detects token swaps, resolves token names, and enforces a premium subscription tier — all powered by an asynchronous pipeline with persistent SQLite storage.

## ✨ Features

### Core Intelligence
- 🔭 **Real-time whale tracking** – Monitors any Solana wallet for SOL transfers and swaps.
- 🔄 **Swap & token detection** – Automatically identifies SPL token swaps and resolves token symbols (e.g., BONK, WIF, JUP) via the Jupiter API.
- 📊 **Balance & transaction history** – Provides pre and post-balance breakdowns.
- ⏸️ **Alert control** – Pause or resume alerts for individual wallets or all at once.

### Premium Subscription Tiers (One‑time Payment)
- **Free Tier**: Track up to 2 wallets.
- **Premium Lite (0.1 SOL)**: Track up to 10 wallets, priority polling, 100 research tokens/month.
- **Premium Pro (0.2 SOL)**: Unlimited wallets, instant priority alerts, full swap detection with token names, 500 research tokens/month.

### Technical Architecture
- ⚡ **Fully asynchronous** – Built with `asyncio`, `aiosqlite`, and `python-telegram-bot`.
- 🗄️ **Persistent storage** – SQLite with WAL mode for concurrent, non-blocking access.
- 🔐 **Secure & atomic** – On-chain payment verification with signature replay protection.
- 🧠 **High‑performance caching** – In-memory TTL caches and database-backed deduplication prevent RPC spam and memory leaks.

## 🚀 Live Demo

The bot is live and running 24/7. Start tracking whales immediately:

➡️ **Telegram:** [@arbimonitor_bot](https://t.me/arbimonitor_bot)

## 📋 Commands

| Command | Description |
|---------|-------------|
| `/start` | Display the main interface and welcome message |
| `/help` | Show all available commands |
| `/benefits` | Compare Free, Lite, and Pro tiers |
| `/pricing` | See premium subscription prices |
| `/premium` | Upgrade your account (Lite: 0.1 SOL, Pro: 0.2 SOL) |
| `/status` | Check your current subscription tier and remaining tokens |
| `/topup` | Add research tokens (100 tokens = 0.01 SOL) |
| `/add <address> <alias>` | Start tracking a new wallet |
| `/remove <address>` | Stop tracking a wallet |
| `/pause <address>` | Pause alerts for a specific wallet |
| `/resume <address>` | Resume alerts for a wallet |
| `/list` | Show all wallets you are currently tracking |
| `/health` | View bot and RPC connection status |
| `/clear` | Clear the chat history |
| `/verify <tx_signature>` | Verify an on-chain SOL payment to activate premium |

## 🛠️ Installation (For Self-Hosting)

### Prerequisites
- Python 3.8+
- `pip` and `virtualenv` (recommended)

### Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/os0b1/arbimonitor.git
   cd arbimonitor
```

2. Create and activate a virtual environment
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. Install dependencies
   ```bash
   pip install -r requirements.txt
   ```
4. Configure environment variables
   Create a .env file in the project root:
   ```env
   TELEGRAM_TOKEN=your_telegram_bot_token
   PREMIUM_WALLET=your_solana_wallet_address
   PREMIUM_PRICE_SOL=0.2
   PREMIUM_LITE_PRICE_SOL=0.1
   PREMIUM_TOLERANCE=0.001
   SOLANA_RPC_URL=https://api.mainnet-beta.solana.com
   ADMIN_CHAT_ID=your_telegram_user_id
   MIN_SOL_THRESHOLD=0.5
   ```
5. Run the bot
   ```bash
   python arbimonitor.py
   ```

🧪 Example Alert

```
🐋 WHALE ALERT 🐋
╔════════════════════════════════════════╗
┃ 👤 Wallet: AlphaWhale
┃ 🔄 Swap: 1000.00 USDC → 12500.00 BONK
┃ 🏦 SOL Balance: 1,234.56 SOL
╚════════════════════════════════════════╝
🔗 View on Solscan: https://solscan.io/tx/5xJx...xyz
```

🗂️ Project Structure

```
arbimonitor/
├── arbimonitor.py          # Main bot application
├── requirements.txt        # Python dependencies
├── .env.example            # Example environment variables
├── .gitignore
└── README.md
```

🧠 Architecture Highlights

· Database: aiosqlite with WAL and check_same_thread=False for safe concurrent async access.
· RPC Client: Singleton AsyncClient with semaphore limiting (5 concurrent calls) to avoid rate limits.
· Alert Queue: asyncio.Queue decouples transaction processing from Telegram delivery (max 10,000 queued alerts).
· Premium Verification: On-chain signature check with amount tolerance (±0.001 SOL) and per‑signature claim deduplication.
· Swap Detection: Compares pre_token_balances and post_token_balances to identify swaps and resolve token symbols via Jupiter API.

📄 License

This project is licensed under the MIT License.

🙏 Acknowledgements

· Built with python-telegram-bot
· Solana RPC integration via solana-py
· Token symbols from Jupiter API
· AI tools (ChatGPT, GitHub Copilot) were used as tutors for debugging and code explanation.

---

⚡ Built raw with Python. No bloat. Just protocol intelligence.

```



