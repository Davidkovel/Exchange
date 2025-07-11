import asyncio

import httpx

from logger import logger

crypto_rates = {
    "BTC": 108000,
    "ETH": 2450,
    "SOL": 150,
    "LTC": 88,
    "XRP": 2.4,
    "TRX": 0.27,
    "DOGE": 0.16,
    "USDT": 1
}

fiat_rates = {
    "RUB": 80,
    "UZB": 12600,
    "USD": 1
}

rates_lock = asyncio.Lock()


async def update_rates():
    while True:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get("https://api.binance.com/api/v3/ticker/price")
                all_prices = {item["symbol"]: float(item["price"]) for item in response.json()}

                async with rates_lock:
                    for symbol in list(crypto_rates.keys()):
                        if symbol == "USDT":
                            continue  # пропускаем USDT, так как это стабильная монета

                        pair = f"{symbol}USDT"  # Binance использует формат BTCUSDT без подчеркивания
                        if pair in all_prices:
                            crypto_rates[symbol] = all_prices[pair]
                            #print(f"Обновлен курс {symbol}: {crypto_rates[symbol]}")

        except Exception as e:
            logger.error(f"[ERROR] Не удалось обновить курс: {e}")

        await asyncio.sleep(3600)  # обновлять каждый час
