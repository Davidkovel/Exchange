import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import requests
import uvicorn
from aiogram.filters import Command
from fastapi import FastAPI, Form, UploadFile, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile, FSInputFile

import aio_pika
from aio_pika.abc import AbstractRobustConnection

from crypto_rates import rates_lock, fiat_rates, crypto_rates, update_rates
from data import JsonService

QUEUE_NAME = "payment_queue"

TELEGRAM_BOT_TOKEN = "7810157984:AAHjcf3NhsFMxo4A1BOEPxZQXkNRRCMMdY0"
TELEGRAM_CHAT_ID = "-1002704025045"

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

payment_router = Router()

json_service = JsonService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    asyncio.create_task(update_rates())
    asyncio.create_task(start_bot())
    connection = await aio_pika.connect_robust("amqp://admin:admin@95.46.107.166:5672/")
    channel = await connection.channel()
    await channel.declare_queue(QUEUE_NAME, durable=True)
    app.state.connection = connection
    app.state.channel = channel
    yield
    # Shutdown
    await connection.close()


app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

UPLOAD_FILE = "persistence"
os.makedirs(UPLOAD_FILE, exist_ok=True)

payment_requests = {}


@app.get("/")
async def exchange_1():
    return FileResponse("templates/exchange_1.html")


@app.get("/payment-details")
async def get_payment_details():
    data = await json_service.load_data()
    return data.get("payment_details", {
        "card_number": "1234 5678 9012 3456",
        "card_holder": "John Doe"
    })


@app.post("/submit-payment")
async def submit_payment(background_tasks: BackgroundTasks, receipt: UploadFile, email: str = Form(...),
                         trx_wallet: str = Form(...),
                         amount: float = Form(...),
                         from_currency: str = Form(...),
                         to_fiat_currency: str = Form(...)):
    try:
        file_location = f"{UPLOAD_FILE}/{uuid.uuid4()}_{receipt.filename}"
        with open(file_location, "wb+") as file_object:
            file_object.write(await receipt.read())

        background_tasks.add_task(
            process_payment_background,  # Функция для фоновой обработки
            file_location=file_location,
            email=email,
            trx_wallet=trx_wallet,
            amount=amount,
            from_currency=from_currency,
            to_fiat_currency=to_fiat_currency
        )

        return {"status": "processing", "message": "Payment is being processed"}
    except Exception as e:
        import traceback
        logger.error("ERROR:", e)
        # traceback.print_exc()
        return {"status": "error", "message": str(e)}


async def process_payment_background(
        file_location: str,
        email: str,
        trx_wallet: str,
        amount: float,
        from_currency: str,
        to_fiat_currency: str
):
    try:
        async with rates_lock:
            # 1. Конвертируем сумму в фиате (to_fiat_currency) в USD
            usd_amount = amount / fiat_rates[to_fiat_currency.upper()]

            # 2. Вычисляем, сколько криптовалюты (from_currency) нужно для получения этой суммы в USD
            crypto_amount = usd_amount / crypto_rates[from_currency.upper()]

            # 3. Конвертируем это количество криптовалюты в USDT
            usdt_amount = crypto_amount * crypto_rates[from_currency.upper()]

        payload = {
            "email": email,
            "wallet_address": trx_wallet,
            "user_balance": usdt_amount,
        }

        payment_requests[email] = payload

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve_{email}")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{email}")]
        ])

        with open(file_location, "rb") as photo_file:
            photo = FSInputFile(file_location)
            await bot.send_photo(
                chat_id=TELEGRAM_CHAT_ID,
                photo=photo,
                caption=f"Новый платеж:\nEmail: {email}\nКошелек: {trx_wallet}\nСумма: {amount} {to_fiat_currency} → {usdt_amount:.2f} USDT",
                reply_markup=keyboard
            )

        Path(file_location).unlink(missing_ok=True)

        return {"status": "Success"}

    except Exception as e:
        import traceback

        logger.error("ERROR:", e)
        # traceback.print_exc()
        return {"status": "error", "message": str(e)}


@payment_router.callback_query(lambda c: c.data.startswith(('approve_', 'reject_')))
async def process_callback(callback_query: types.CallbackQuery):
    email = callback_query.data.split('_')[1]
    payload = payment_requests.get(email)

    if callback_query.data.startswith('approve_'):
        if payload:
            message = aio_pika.Message(
                body=json.dumps(payload).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT
            )

            await app.state.channel.default_exchange.publish(
                message,
                routing_key=QUEUE_NAME
            )

    else:
        pass
        # await callback_query.answer("Платеж отклонен")

    payment_requests.pop(email, None)

    await bot.delete_message(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id
    )


@payment_router.message(Command("edit_payment"))
async def start_edit(message: types.Message):
    await message.answer(
        "Введите новые платежные данные в формате:\n"
        "<code>Номер карты | Имя держателя</code>\n\n"
        "Пример:\n"
        "<code>5555 6666 7777 8888 | Иван Петров</code>",
        parse_mode="HTML"
    )


@payment_router.message(F.text.contains("|"))
async def process_payment_update(message: types.Message):
    try:
        if "|" in message.text:
            card_number, card_holder = map(str.strip, message.text.split("|", 1))

            # Загружаем текущие данные
            data = await json_service.load_data()

            # Обновляем только платежные данные
            data["payment_details"] = {
                "card_number": card_number,
                "card_holder": card_holder
            }

            # Сохраняем обратно
            if await json_service.save_data(data):
                await message.answer("✅ Данные успешно обновлены!")
            else:
                await message.answer("❌ Ошибка при сохранении данных")

    except Exception as e:
        await message.answer(f"❌ Ошибка формата: {str(e)}\n\n"
                             "Используйте формат: <code>Номер карты | Имя держателя</code>",
                             parse_mode="HTML")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # или конкретный домен вместо "*"
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def start_bot():
    await dp.start_polling(bot)

def main():
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    main()
