"""
Micro-trade proof-of-life script for Bitget UTA account.
Buys ~$5 of a liquid token, waits 5s, sells it back.
Records everything to live_trade_proof.json.
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone

import ccxt.async_support as ccxt
from dotenv import load_dotenv

load_dotenv(override=True)

TRADE_SIZE_USD = 5.0


async def main():
    # -- Connect --
    exchange = ccxt.bitget({
        "apiKey": os.environ["BITGET_API_KEY"],
        "secret": os.environ["BITGET_API_SECRET"],
        "password": os.environ["BITGET_PASSPHRASE"],
        "sandbox": False,
        "timeout": 30000,
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
            "uta": True,
        },
    })

    try:
        # -- Load markets --
        print("[1] Loading markets...")
        await exchange.load_markets()

        # -- Balance before --
        print("[2] Fetching balance...")
        bal = await exchange.fetch_balance()
        usdt_bal = bal.get("USDT", {})
        balance_before = float(usdt_bal.get("free", 0))
        print(f"    USDT free: {balance_before:.4f}")

        if balance_before < TRADE_SIZE_USD:
            print(f"ERROR: Insufficient balance ({balance_before} < {TRADE_SIZE_USD})")
            return

        # -- Pick symbol with lowest min order --
        candidates = ["BTC/USDT", "SOL/USDT"]
        best_symbol = None
        best_min_cost = float("inf")

        for sym in candidates:
            mkt = exchange.markets.get(sym)
            if not mkt:
                continue
            min_cost = float(mkt.get("limits", {}).get("cost", {}).get("min", 0) or 0)
            min_amount = float(mkt.get("limits", {}).get("amount", {}).get("min", 0) or 0)
            ticker = await exchange.fetch_ticker(sym)
            price = float(ticker["last"])
            effective_min = max(min_cost, min_amount * price)
            print(f"    {sym}: price=${price:.2f}, minCost=${min_cost}, minAmt={min_amount}, effective_min=${effective_min:.4f}")
            if effective_min < best_min_cost and effective_min <= TRADE_SIZE_USD:
                best_min_cost = effective_min
                best_symbol = sym

        if not best_symbol:
            print("ERROR: No suitable symbol found within $5 min order")
            return

        symbol = best_symbol
        print(f"\n[3] Selected: {symbol}")

        # -- Get price --
        mkt = exchange.markets[symbol]
        ticker = await exchange.fetch_ticker(symbol)
        price_now = float(ticker["last"])
        print(f"    Price: ${price_now:.4f}")

        # -- BUY using cost (quote amount) --
        # Bitget UTA requires specifying cost for market buys
        print(f"\n[4] Placing BUY market order: ${TRADE_SIZE_USD} worth of {symbol}...")
        buy_order = await exchange.create_order(
            symbol=symbol,
            type="market",
            side="buy",
            amount=TRADE_SIZE_USD,
            params={"quoteOrderQty": TRADE_SIZE_USD},
        )
        print(f"    Buy order placed: {buy_order.get('id')}")
        print(f"    Status: {buy_order.get('status')}")

        # Wait for fill data
        await asyncio.sleep(1)
        buy_order = await exchange.fetch_order(buy_order["id"], symbol)

        buy_id = buy_order.get("id", "unknown")
        buy_price = float(buy_order.get("average", 0) or buy_order.get("price", 0) or price_now)
        buy_qty = float(buy_order.get("filled", 0) or TRADE_SIZE_USD / price_now)
        buy_cost = float(buy_order.get("cost", 0) or buy_price * buy_qty)

        print(f"    FILLED: price=${buy_price:.4f}, qty={buy_qty}, cost=${buy_cost:.4f}")

        # -- Wait --
        print("\n[5] Waiting 5 seconds...")
        await asyncio.sleep(5)

        # -- SELL --
        # Check actual base currency balance (fees may have been deducted)
        bal_mid = await exchange.fetch_balance()
        base_currency = symbol.split("/")[0]
        actual_base_free = float(bal_mid.get(base_currency, {}).get("free", 0))
        sell_qty = min(buy_qty, actual_base_free)
        # Apply precision
        sell_qty = float(exchange.amount_to_precision(symbol, sell_qty))
        print(f"\n[6] Placing SELL market order: {sell_qty} {symbol} (available: {actual_base_free})...")
        sell_order = await exchange.create_order(
            symbol=symbol,
            type="market",
            side="sell",
            amount=sell_qty,
        )
        print(f"    Sell order placed: {sell_order.get('id')}")

        await asyncio.sleep(1)
        sell_order = await exchange.fetch_order(sell_order["id"], symbol)

        sell_id = sell_order.get("id", "unknown")
        sell_price = float(sell_order.get("average", 0) or sell_order.get("price", 0) or price_now)
        sell_qty_filled = float(sell_order.get("filled", 0) or sell_qty)
        sell_cost = float(sell_order.get("cost", 0) or sell_price * sell_qty_filled)

        print(f"    FILLED: price=${sell_price:.4f}, qty={sell_qty_filled}, cost=${sell_cost:.4f}")

        # -- PnL --
        pnl = sell_cost - buy_cost
        print(f"\n[7] PnL: ${pnl:.6f}")

        # -- Balance after --
        bal_after = await exchange.fetch_balance()
        usdt_after = bal_after.get("USDT", {})
        balance_after = float(usdt_after.get("free", 0))
        print(f"    Balance after: {balance_after:.4f} USDT")

        # -- Save results --
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "buy_order_id": buy_id,
            "buy_price": round(buy_price, 6),
            "buy_qty": round(buy_qty, 8),
            "buy_cost": round(buy_cost, 6),
            "sell_order_id": sell_id,
            "sell_price": round(sell_price, 6),
            "sell_qty": round(sell_qty_filled, 8),
            "sell_cost": round(sell_cost, 6),
            "pnl_usd": round(pnl, 6),
            "balance_before": round(balance_before, 4),
            "balance_after": round(balance_after, 4),
        }

        out_path = "/workspace/output/runeclaw/live_trade_proof.json"
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\n[8] Results saved to {out_path}")
        print(json.dumps(result, indent=2))

    except Exception as e:
        print(f"\nERROR: {type(e).__name__}: {e}")
        raise
    finally:
        await exchange.close()


if __name__ == "__main__":
    asyncio.run(main())
