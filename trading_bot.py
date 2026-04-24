import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

import os
TOKEN = os.environ.get("TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# ── Pair config: (pip_value_per_lot, pip_size) ──────────────────────────────
# pip_value_per_lot = dollar value of 1 point movement on 1 full lot
PAIR_CONFIG = {
    "V75":       {"pip_value": 1.0,    "label": "V75"},
    "V75(1S)":   {"pip_value": 0.1,    "label": "V75 (1s)"},
    "V75 1S":    {"pip_value": 0.1,    "label": "V75 (1s)"},
    "V75-1S":    {"pip_value": 0.1,    "label": "V75 (1s)"},
    "STEP100":   {"pip_value": 1.0,    "label": "Step Index 100"},
    "STEP200":   {"pip_value": 1.0,    "label": "Step Index 200"},
    "GBPUSD":    {"pip_value": 10.0,   "label": "GBP/USD"},
    "GBPJPY":    {"pip_value": 6.5,    "label": "GBP/JPY"},
    "EURUSD":    {"pip_value": 10.0,   "label": "EUR/USD"},
    "USDJPY":    {"pip_value": 6.5,    "label": "USD/JPY"},
    "XAUUSD":    {"pip_value": 10.0,   "label": "XAU/USD (Gold)"},
    "BTCUSD":    {"pip_value": 1.0,    "label": "BTC/USD"},
}


def normalise_pair(raw: str) -> str | None:
    key = raw.upper().replace(" ", "").replace("_", "")
    for k in PAIR_CONFIG:
        if k.replace(" ", "").replace("(", "").replace(")", "").replace("-","") == key.replace("(","").replace(")","").replace("-",""):
            return k
    # fallback direct match
    return key if key in PAIR_CONFIG else None


def risk_colour(pct: float) -> str:
    if pct <= 1:
        return "🟢"
    if pct <= 2:
        return "🟡"
    if pct <= 5:
        return "🟠"
    return "🔴"


def build_message(pair_key: str, sl_points: float, lot_size: float,
                  risk_dollars: float | None, account: float | None) -> str:
    cfg = PAIR_CONFIG[pair_key]
    label = cfg["label"]
    pip_val = cfg["pip_value"]          # $ per point per full lot

    # ── Core risk calculation ───────────────────────────────────────────────
    if risk_dollars is not None:
        # User supplied a $ risk amount → derive the effective lot for display
        risk = risk_dollars
        # Back-calculate lot size that would produce this risk
        risk_per_lot = sl_points * pip_val
        derived_lot = risk / risk_per_lot if risk_per_lot else 0
        display_lot = derived_lot
    else:
        risk_per_lot = sl_points * pip_val
        risk = lot_size * risk_per_lot
        display_lot = lot_size

    profit_2r = risk * 2
    profit_3r = risk * 3

    lines = [
        "📊 *TRADE CALCULATOR*",
        f"Pair: *{label}*",
        f"Lot Size: *{display_lot:.2f}*",
        f"SL: *{sl_points} points*",
        "─────────────────",
        "💰 *RISK & REWARD*",
        "─────────────────",
        f"❌ Risk (SL hit): *${risk:.2f}*",
        f"🎯 Profit at 2R: *${profit_2r:.2f}*",
        f"🚀 Profit at 3R: *${profit_3r:.2f}*",
        "─────────────────",
        "📈 *SCALING TABLE*",
        "─────────────────",
    ]

    # Show 4 multiples of the base lot
    base = display_lot
    for mult in [1, 2, 3, 4]:
        l = base * mult
        r = risk * mult
        p = profit_3r * mult
        lines.append(f"{l:.2f} lot → Risk ${r:.2f} | 3R = ${p:.2f}")

    # ── Account / risk % section ────────────────────────────────────────────
    if account is not None and account > 0:
        pct = (risk / account) * 100
        colour = risk_colour(pct)
        rec_min = risk / 0.01   # 1 % threshold
        rec_max = risk / 0.02   # 2 % threshold

        lines += [
            "─────────────────",
            "⚠️ *RISK WARNING*",
            "─────────────────",
            f"Account: *${account:.2f}*",
            f"Risk: *${risk:.2f}* ({pct:.1f}% of account)",
            f"{colour} {'Safe risk level' if pct <= 2 else 'Warning: High % risk per trade'}",
            f"Recommended max risk: 1-2% = ${rec_max:.2f}-${rec_min:.2f}",
        ]

    return "\n".join(lines)


def parse_input(text: str):
    """
    Accepted formats (space-separated):
      PAIR  SL  LOT  [ACCOUNT]
      PAIR  SL  $RISK_AMOUNT  [ACCOUNT]

    Returns (pair_key, sl, lot_or_None, risk_dollars_or_None, account_or_None)
    """
    parts = text.strip().split()
    if len(parts) < 3:
        return None, "Need at least: PAIR  SL  LOT_or_$RISK"

    raw_pair = parts[0]
    pair_key = normalise_pair(raw_pair)
    if pair_key is None or pair_key not in PAIR_CONFIG:
        supported = ", ".join(PAIR_CONFIG.keys())
        return None, f"Unknown pair *{raw_pair}*.\nSupported: {supported}"

    try:
        sl = float(parts[1])
    except ValueError:
        return None, "SL must be a number (e.g. 17.76)"

    # 3rd argument: lot size OR $risk_amount
    third = parts[2]
    risk_dollars = None
    lot_size = None

    if third.startswith("$"):
        try:
            risk_dollars = float(third[1:])
        except ValueError:
            return None, "Dollar risk must look like $10 or $10.50"
    else:
        try:
            lot_size = float(third)
        except ValueError:
            return None, "Lot size must be a number or use $amount for risk (e.g. $10)"

    account = None
    if len(parts) >= 4:
        try:
            account = float(parts[3])
        except ValueError:
            return None, "Account balance must be a number (e.g. 200)"

    return (pair_key, sl, lot_size, risk_dollars, account), None


# ── Handlers ────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *Welcome to the Trade Calculator Bot!*\n\n"
        "Send a trade in one of these formats:\n\n"
        "`PAIR  SL  LOT  [ACCOUNT]`\n"
        "`PAIR  SL  $RISK  [ACCOUNT]`\n\n"
        "*Examples:*\n"
        "`V75 17.76 0.05 200`  → lot-based, $200 account\n"
        "`V75 17.76 $10 500`   → risk $10, $500 account\n"
        "`GBPUSD 35 0.10`      → no account balance\n\n"
        "Use /pairs to see all supported pairs.\n"
        "Use /help for more info."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📖 *How to use this bot*\n\n"
        "*Format 1 – Lot size:*\n"
        "`PAIR  SL_POINTS  LOT_SIZE  [ACCOUNT]`\n\n"
        "*Format 2 – Dollar risk:*\n"
        "`PAIR  SL_POINTS  $RISK_AMOUNT  [ACCOUNT]`\n\n"
        "• `PAIR` – trading pair (e.g. V75, GBPUSD)\n"
        "• `SL_POINTS` – stop loss distance in points\n"
        "• `LOT_SIZE` – e.g. 0.05\n"
        "• `$RISK_AMOUNT` – e.g. $10 (bot calculates lot for you)\n"
        "• `ACCOUNT` – optional balance for risk % warning\n\n"
        "Use /pairs to see all supported instruments."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def pairs_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = [f"• *{v['label']}* → `{k}`" for k, v in PAIR_CONFIG.items()
            if not any(k == alt for alt in ["V75(1S)", "V75-1S"])]
    msg = "📋 *Supported Pairs*\n\n" + "\n".join(rows)
    await update.message.reply_text(msg, parse_mode="Markdown")


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    result, err = parse_input(text)

    if err:
        await update.message.reply_text(f"❌ {err}", parse_mode="Markdown")
        return

    pair_key, sl, lot_size, risk_dollars, account = result
    msg = build_message(pair_key, sl, lot_size, risk_dollars, account)
    await update.message.reply_text(msg, parse_mode="Markdown")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("pairs", pairs_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot is running... Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
