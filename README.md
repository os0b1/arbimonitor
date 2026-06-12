# Whale Tracer

Real-time Solana whale transaction monitor.

## Features
- Live monitoring of any Solana wallet
- Alerts when new transactions occur
- Links to Solscan for transaction details

## Requirements
- Python 3.8+
- `solana` and `solders` Python packages

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/os0b1/whale_tracer.git
   cd whale_tracer
```

2. (Optional) Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate   # On Windows: .venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

Usage

Run the script to start monitoring the configured whale address:

```bash
python whale_trace.py
```

The script will continuously check for new transactions. When a new transaction is detected, it prints an alert and a link to Solscan.

Press Ctrl+C to stop.

Configuration

Open whale_trace.py and modify the following variables at the top of the file:

· RPC_URL – Your Solana RPC endpoint (e.g., QuickNode, Helius, or the public mainnet endpoint)
· TARGET_WHALE – The public key of the wallet address you want to monitor

Example Output

```
STARTING LIVE MONITOR ON: EmDewJpfQaxWqxtxhX1FyBCCPNGt8Ac5ek4M4pnGTgxc
ALERT: WHALE MOVEMENT DETECTED!
https://solscan.io/tx/5xJx...xyz
```

License

MIT

```

---

## How to save this file

### In VS Code:
1. Create a new file called `README.md` in your `whale_tracer` folder
2. Copy the entire block above
3. Paste it into the file
4. Save (`Ctrl+S`)

## Acknowledgements

This project was built from scratch by me, but I used AI tools (ChatGPT, GitHub Copilot) to help with:
- Debugging indentation and syntax errors
- Understanding async patterns and Solana RPC quirks
- Optimizing the transaction parsing logic
- Writing error handling for edge cases

All code was manually typed, reviewed, and understood. AI was a tutor, not a writer.