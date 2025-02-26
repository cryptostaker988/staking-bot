import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import F
from aiogram.filters import Command
import sqlite3
import os
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
import urllib.parse
from aiohttp import web
import hmac
import hashlib
import json

API_TOKEN = os.getenv("API_TOKEN", "8035474937:AAHoXggpaaEZbxpYJi_rAPSkYr_HMWhuxy4")
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY", "your_api_key")
IPN_SECRET = os.getenv("IPN_SECRET", "your_ipn_secret")
ADMIN_ID = None
logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dispatcher = Dispatcher()
app = web.Application()

# قفل برای همگام‌سازی دسترسی به دیتابیس
db_lock = asyncio.Lock()

# اتصال به دیتابیس SQLite با قفل
async def db_connect():
    async with db_lock:
        try:
            conn = sqlite3.connect("staking_bot.db", timeout=10)
            return conn
        except sqlite3.OperationalError as e:
            logging.error(f"Failed to connect to database: {e}")
            raise

# بررسی و ایجاد/به‌روزرسانی جداول
async def initialize_database():
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                            user_id INTEGER PRIMARY KEY,
                            username TEXT,
                            balance_usdt REAL DEFAULT 0,
                            balance_trx REAL DEFAULT 0,
                            earnings_usdt REAL DEFAULT 0,
                            earnings_trx REAL DEFAULT 0,
                            last_earning_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )''')
        
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN balance_usdt REAL DEFAULT 0")
            cursor.execute("ALTER TABLE users ADD COLUMN balance_trx REAL DEFAULT 0")
            cursor.execute("ALTER TABLE users ADD COLUMN earnings_usdt REAL DEFAULT 0")
            cursor.execute("ALTER TABLE users ADD COLUMN earnings_trx REAL DEFAULT 0")
            cursor.execute("ALTER TABLE users ADD COLUMN last_earning_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            logging.info("Updated users table with new columns.")
        except sqlite3.OperationalError:
            pass

        cursor.execute('''CREATE TABLE IF NOT EXISTS transactions (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER,
                            transaction_type TEXT,
                            amount REAL,
                            currency TEXT,
                            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS stakes (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER,
                            plan_id INTEGER,
                            amount REAL,
                            currency TEXT,
                            start_date TIMESTAMP,
                            duration_days INTEGER,
                            last_earning_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            is_expired INTEGER DEFAULT 0
                        )''')
        
        try:
            cursor.execute("ALTER TABLE stakes ADD COLUMN currency TEXT")
            logging.info("Added currency column to stakes table.")
        except sqlite3.OperationalError:
            pass

        cursor.execute('''CREATE TABLE IF NOT EXISTS withdraw_requests (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER,
                            amount REAL,
                            currency TEXT,
                            fee REAL,
                            wallet_address TEXT,
                            status TEXT DEFAULT 'Pending',
                            request_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS wallets (
                            user_id INTEGER,
                            currency TEXT,
                            wallet_address TEXT,
                            deposit_address TEXT,
                            PRIMARY KEY (user_id, currency)
                        )''')
        
        conn.commit()
        conn.close()
        logging.info("Database initialized successfully.")

# افزودن یا به‌روزرسانی کاربر
async def add_user(user_id, username):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        if user:
            cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
        else:
            cursor.execute("INSERT INTO users (user_id, username, last_earning_update) VALUES (?, ?, ?)", 
                          (user_id, username, datetime.now()))
        conn.commit()
        conn.close()

# دریافت اطلاعات کاربر
async def get_user(user_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        conn.close()
        return user
    return None

# دریافت همه استیک‌های کاربر
async def get_user_stakes(user_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM stakes WHERE user_id = ?", (user_id,))
        stakes = cursor.fetchall()
        conn.close()
        return stakes
    return []

# دریافت استیک‌های فعال کاربر
async def get_active_stakes(user_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM stakes WHERE user_id = ?", (user_id,))
        all_stakes = cursor.fetchall()
        conn.close()
        
        active_stakes = []
        now = datetime.now()
        for stake in all_stakes:
            if len(stake) == 8:  # بدون currency
                stake_id, _, plan_id, amount, start_date, duration_days, last_update, is_expired = stake
                currency = "USDT"  # پیش‌فرض برای سازگاری با داده‌های قدیمی
            else:
                stake_id, _, plan_id, amount, currency, start_date, duration_days, last_update, is_expired = stake
            
            start_date = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S.%f')
            days_passed = (now - start_date).total_seconds() / (24 * 3600)
            
            if (duration_days is None or days_passed < duration_days) and is_expired == 0:
                active_stakes.append(stake)
        
        return active_stakes
    return []

# به‌روزرسانی موجودی
async def update_balance(user_id, amount, currency):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT balance_usdt, balance_trx FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        if user:
            balance_usdt, balance_trx = user
            if currency == "USDT":
                new_balance = balance_usdt + amount
                if new_balance < 0:
                    conn.close()
                    return False
                cursor.execute("UPDATE users SET balance_usdt = ? WHERE user_id = ?", (new_balance, user_id))
            elif currency == "TRX":
                new_balance = balance_trx + amount
                if new_balance < 0:
                    conn.close()
                    return False
                cursor.execute("UPDATE users SET balance_trx = ? WHERE user_id = ?", (new_balance, user_id))
            conn.commit()
        else:
            if currency == "USDT":
                cursor.execute("INSERT INTO users (user_id, balance_usdt) VALUES (?, ?)", (user_id, amount))
            elif currency == "TRX":
                cursor.execute("INSERT INTO users (user_id, balance_trx) VALUES (?, ?)", (user_id, amount))
            conn.commit()
        conn.close()
        return True
    return False

# به‌روزرسانی سود کاربر
async def update_earnings(user_id, earnings_change, currency):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT earnings_usdt, earnings_trx FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        if user:
            earnings_usdt, earnings_trx = user
            if currency == "USDT":
                new_earnings = earnings_usdt + earnings_change
                if new_earnings < 0:
                    conn.close()
                    return False
                cursor.execute("UPDATE users SET earnings_usdt = ?, last_earning_update = ? WHERE user_id = ?", 
                              (new_earnings, datetime.now(), user_id))
            elif currency == "TRX":
                new_earnings = earnings_trx + earnings_change
                if new_earnings < 0:
                    conn.close()
                    return False
                cursor.execute("UPDATE users SET earnings_trx = ?, last_earning_update = ? WHERE user_id = ?", 
                              (new_earnings, datetime.now(), user_id))
            conn.commit()
        conn.close()
        return True
    return False

# اضافه کردن استیک
async def add_stake(user_id, plan_id, amount, duration_days, currency):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO stakes (user_id, plan_id, amount, currency, start_date, duration_days, last_earning_update, is_expired) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (user_id, plan_id, amount, currency, datetime.now(), duration_days, datetime.now(), 0))
        conn.commit()
        conn.close()
        logging.info(f"Stake added: user_id={user_id}, plan_id={plan_id}, amount={amount}, currency={currency}, duration_days={duration_days}")
        return True
    return False

# محاسبه سود استیک‌ها و به‌روزرسانی
async def calculate_total_earnings(user_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT earnings_usdt, earnings_trx FROM users WHERE user_id = ?", (user_id,))
        earnings = cursor.fetchone()
        past_earnings_usdt, past_earnings_trx = earnings if earnings else (0, 0)
        
        stakes = await get_user_stakes(user_id)
        total_new_earnings_usdt = 0
        total_new_earnings_trx = 0
        now = datetime.now()
        
        for stake in stakes:
            if len(stake) == 8:  # بدون currency
                stake_id, _, plan_id, amount, start_date, duration_days, last_update, is_expired = stake
                currency = "USDT"  # پیش‌فرض برای سازگاری
            else:
                stake_id, _, plan_id, amount, currency, start_date, duration_days, last_update, is_expired = stake
            
            start_date = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S.%f')
            last_update = datetime.strptime(last_update, '%Y-%m-%d %H:%M:%S.%f') if isinstance(last_update, str) else last_update
            days_passed = (now - start_date).total_seconds() / (24 * 3600)
            days_since_last = (now - last_update).total_seconds() / (24 * 3600)
            
            profit_rate = {1: 0.02, 2: 0.03, 3: 0.04, 4: 0.04, 5: 0.03, 6: 0.02}[plan_id]
            
            if duration_days is None or days_passed < duration_days:
                total_days = int(days_passed)
                stake_earnings = amount * profit_rate * total_days
                new_days = int(days_since_last)
                if new_days > 0:
                    new_earnings = amount * profit_rate * new_days
                    if currency == "USDT":
                        total_new_earnings_usdt += new_earnings
                    elif currency == "TRX":
                        total_new_earnings_trx += new_earnings
                    cursor.execute("UPDATE stakes SET last_earning_update = ? WHERE id = ?", (now, stake_id))
            elif days_passed >= duration_days and is_expired == 0:
                stake_earnings = amount * profit_rate * duration_days
                if currency == "USDT":
                    total_new_earnings_usdt += stake_earnings
                elif currency == "TRX":
                    total_new_earnings_trx += stake_earnings
                cursor.execute("UPDATE stakes SET last_earning_update = ?, is_expired = 1 WHERE id = ?", (now, stake_id))
        
        if total_new_earnings_usdt > 0:
            await update_earnings(user_id, total_new_earnings_usdt, "USDT")
        if total_new_earnings_trx > 0:
            await update_earnings(user_id, total_new_earnings_trx, "TRX")
        
        cursor.execute("SELECT earnings_usdt, earnings_trx FROM users WHERE user_id = ?", (user_id,))
        earnings_usdt, earnings_trx = cursor.fetchone()
        conn.commit()
        conn.close()
        return earnings_usdt, earnings_trx
    return 0, 0

# ثبت تراکنش
async def add_transaction(user_id, transaction_type, amount, currency):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO transactions (user_id, transaction_type, amount, currency) VALUES (?, ?, ?, ?)", 
                      (user_id, transaction_type, amount, currency))
        conn.commit()
        conn.close()
        logging.info(f"Transaction added: {transaction_type} {amount} {currency} for user {user_id}")

# انتقال سود به بالانس با مقدار دلخواه
async def transfer_earnings_to_balance(user_id, amount, currency):
    user = await get_user(user_id)
    if user:
        earnings_usdt, earnings_trx = user[4], user[5]  # ستون‌های earnings_usdt و earnings_trx
        earnings = earnings_usdt if currency == "USDT" else earnings_trx
        if amount > 0 and amount <= earnings:
            if await update_balance(user_id, amount, currency) and await update_earnings(user_id, -amount, currency):
                await add_transaction(user_id, "earnings_transfer", amount, currency)
                user = await get_user(user_id)
                new_balance = user[2] if currency == "USDT" else user[3]  # balance_usdt یا balance_trx
                return True, f"{amount:.2f} {currency} has been transferred to your balance. New {currency} balance: {new_balance:.2f} {currency}"
            else:
                return False, "Failed to transfer earnings. Try again."
        else:
            return False, f"You don’t have enough earnings. Your current {currency} earnings: {earnings:.2f} {currency}"
    return False, "User not found."

# دریافت آدرس کیف پول کاربر
async def get_wallet_address(user_id, currency):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT wallet_address FROM wallets WHERE user_id = ? AND currency = ?", (user_id, currency))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    return None

# دریافت آدرس واریز کاربر
async def get_deposit_address(user_id, currency):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT deposit_address FROM wallets WHERE user_id = ? AND currency = ?", (user_id, currency))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    return None

# ذخیره یا به‌روزرسانی آدرس کیف پول
async def save_wallet_address(user_id, currency, wallet_address):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO wallets (user_id, currency, wallet_address) VALUES (?, ?, ?)",
                      (user_id, currency, wallet_address))
        conn.commit()
        conn.close()

# ذخیره آدرس واریز
async def save_deposit_address(user_id, currency, address):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE wallets SET deposit_address = ? WHERE user_id = ? AND currency = ?",
                      (address, user_id, currency))
        if cursor.rowcount == 0:
            cursor.execute("INSERT INTO wallets (user_id, currency, deposit_address) VALUES (?, ?, ?)",
                          (user_id, currency, address))
        conn.commit()
        conn.close()

# تولید آدرس واریز با NOWPayments
async def generate_payment_address(user_id, amount, currency):
    headers = {"x-api-key": NOWPAYMENTS_API_KEY}
    payload = {
        "price_amount": amount,
        "price_currency": currency.lower(),
        "pay_currency": currency.lower(),
        "order_id": str(user_id),
        "ipn_callback_url": "https://staking-bot.onrender.com/webhook"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://api.nowpayments.io/v1/payment", json=payload, headers=headers) as resp:
            data = await resp.json()
            logging.info(f"NOWPayments response: {data}")  # لاگ برای دیباگ
            if "pay_address" in data:
                return data["pay_address"]
            else:
                logging.error(f"Error from NOWPayments: {data}")
                raise Exception(f"Failed to get pay_address: {data.get('message', 'Unknown error')}")

# چک کردن آخرین درخواست برداشت
async def check_last_withdrawal(user_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, request_time FROM withdraw_requests WHERE user_id = ? ORDER BY request_time DESC LIMIT 1", (user_id,))
        result = cursor.fetchone()
        conn.close()
        if result:
            status, request_time = result
            request_time = datetime.strptime(request_time, '%Y-%m-%d %H:%M:%S.%f')
            return status, request_time
        return None, None

# ثبت درخواست برداشت
async def add_withdraw_request(user_id, amount, currency, fee, wallet_address):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO withdraw_requests (user_id, amount, currency, fee, wallet_address, status, request_time) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (user_id, amount, currency, fee, wallet_address, "Pending", datetime.now()))
        conn.commit()
        conn.close()
        logging.info(f"Withdrawal request added: user_id={user_id}, amount={amount}, currency={currency}")

# گرفتن درخواست‌های معلق 12 ساعت گذشته
async def get_pending_withdrawals():
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        twelve_hours_ago = datetime.now() - timedelta(hours=12)
        cursor.execute("SELECT * FROM withdraw_requests WHERE status = 'Pending' AND request_time >= ?", (twelve_hours_ago,))
        requests = cursor.fetchall()
        conn.close()
        logging.info(f"Fetched {len(requests)} pending withdrawals.")
        return requests
    return []

# به‌روزرسانی وضعیت درخواست به تکمیل‌شده
async def complete_withdrawal(request_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE withdraw_requests SET status = 'Completed' WHERE id = ?", (request_id,))
        conn.commit()
        conn.close()

# به‌روزرسانی وضعیت درخواست به ردشده
async def reject_withdrawal(request_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE withdraw_requests SET status = 'Rejected' WHERE id = ?", (request_id,))
        conn.commit()
        conn.close()

# گرفتن اطلاعات درخواست برای ارسال پیام به کاربر
async def get_withdrawal_details(request_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, amount, currency, wallet_address FROM withdraw_requests WHERE id = ?", (request_id,))
        result = cursor.fetchone()
        conn.close()
        return result
    return None

# کارمزد ثابت
def get_withdrawal_fee(currency):
    if currency == "USDT":
        return 1.5  # کارمزد ثابت USDT
    elif currency == "TRX":
        return 1.0  # کارمزد ثابت TRX
    return 1.5  # پیش‌فرض

# ارسال گزارش به مدیر با دکمه‌ها
async def send_withdrawal_report():
    global ADMIN_ID
    if ADMIN_ID is None:
        logging.info("Admin ID not set, skipping report.")
        return
    
    requests = await get_pending_withdrawals()
    if not requests:
        await bot.send_message(ADMIN_ID, "No pending withdrawals in the last 12 hours.")
        return
    
    report = "Pending Withdrawals (last 12 hours):\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for req in requests:
        report += f"ID: {req[0]} | User: {req[1]} | Amount: {req[2]:.2f} {req[3]} | Fee: {req[4]:.2f} {req[3]} | Address: {req[5]} | Time: {req[7]}\n"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"Complete ID {req[0]}", callback_data=f"complete_{req[0]}"),
            InlineKeyboardButton(text=f"Reject ID {req[0]}", callback_data=f"reject_{req[0]}")
        ])
    
    await bot.send_message(ADMIN_ID, report, reply_markup=keyboard)

# تابع زمان‌بندی برای ارسال گزارش
async def schedule_reports():
    while True:
        await send_withdrawal_report()
        await asyncio.sleep(43200)  # هر 12 ساعت

# تعریف State‌ها
class DepositState(StatesGroup):
    selecting_currency = State()
    waiting_for_amount = State()

class WithdrawState(StatesGroup):
    selecting_currency = State()
    confirming_address = State()
    entering_new_address = State()
    entering_amount = State()

class StakeState(StatesGroup):
    selecting_currency = State()
    selecting_plan = State()
    waiting_for_amount = State()

class EarningsState(StatesGroup):
    choosing_action = State()
    entering_amount = State()

# منوی اصلی با اموجی‌ها
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💰 Deposit"), KeyboardButton(text="💳 Withdraw")],
        [KeyboardButton(text="💸 Stake"), KeyboardButton(text="💼 Check Balance")],
        [KeyboardButton(text="📋 Check Staked"), KeyboardButton(text="📈 View Earnings")],
        [KeyboardButton(text="👥 Referral Link")]
    ],
    resize_keyboard=True
)

# منوی انتخاب ارز برای دیپازیت
deposit_currency_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Deposit USDT"), KeyboardButton(text="Deposit TRX")],
        [KeyboardButton(text="Back to Main Menu")]
    ],
    resize_keyboard=True
)

# منوی انتخاب ارز برای استیک
stake_currency_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Stake USDT"), KeyboardButton(text="Stake TRX")],
        [KeyboardButton(text="Back to Main Menu")]
    ],
    resize_keyboard=True
)

# منوی پلن‌های استیک (مشترک برای USDT و TRX)
stake_plan_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Starter 2%"), KeyboardButton(text="Pro 3%")],
        [KeyboardButton(text="Elite 4%"), KeyboardButton(text="40-Day Boost")],
        [KeyboardButton(text="60-Day Gain"), KeyboardButton(text="100-Day Steady")],
        [KeyboardButton(text="Back to Main Menu")]
    ],
    resize_keyboard=True
)

# منوی انتخاب ارز برای برداشت
withdraw_currency_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Withdraw USDT"), KeyboardButton(text="Withdraw TRX")],
        [KeyboardButton(text="Back to Main Menu")]
    ],
    resize_keyboard=True
)

# منوی تأیید آدرس
address_confirmation_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Yes"), KeyboardButton(text="Change Address")],
        [KeyboardButton(text="Back to Main Menu")]
    ],
    resize_keyboard=True
)

# منوی انتخاب عمل برای سود
earnings_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Back to Main Menu"), KeyboardButton(text="Transfer to Balance")]
    ],
    resize_keyboard=True
)

# وب‌هوک برای NOWPayments
async def handle_webhook(request):
    signature = request.headers.get("x-nowpayments-sig")
    body = await request.text()
    data = json.loads(body)

    computed_sig = hmac.new(IPN_SECRET.encode(), body.encode(), hashlib.sha512).hexdigest()
    if computed_sig != signature:
        return web.Response(text="Invalid signature", status=403)

    user_id = int(data.get("order_id"))
    amount = float(data["payment_amount"])
    currency = data["pay_currency"].upper()

    await update_balance(user_id, amount, currency)
    await add_transaction(user_id, "deposit", amount, currency)
    await bot.send_message(user_id, f"Your deposit of {amount:.2f} {currency} has been credited!")

    return web.Response(text="Success")

app.router.add_post('/webhook', handle_webhook)

# دستورات منوی آبی‌رنگ
@dispatcher.message(Command("start"))
async def send_welcome(message: types.Message):
    global ADMIN_ID
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    await add_user(user_id, username)
    if username.lower() == "kanka1":
        ADMIN_ID = user_id
        logging.info(f"Admin ID set to: {ADMIN_ID}")
    await message.reply("Welcome to the Staking Bot! Choose an option:", reply_markup=main_menu)

@dispatcher.message(Command("deposit"))
async def deposit_command(message: types.Message, state: FSMContext):
    await message.reply("Choose a currency to deposit:", reply_markup=deposit_currency_menu)
    await state.set_state(DepositState.selecting_currency)

@dispatcher.message(Command("withdraw"))
async def withdraw_command(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    status, last_request_time = await check_last_withdrawal(user_id)
    now = datetime.now()
    
    if status:
        time_diff = now - last_request_time
        if time_diff.total_seconds() < 24 * 3600:  # کمتر از 24 ساعت
            if status == "Pending":
                await message.reply("You already have a pending withdrawal request. Please wait until it’s processed.", reply_markup=main_menu)
            else:
                await message.reply(f"You’ve already submitted a request. Please wait 24 hours from your last request (submitted at {last_request_time}).", reply_markup=main_menu)
            return
    
    await message.reply("Choose a currency to withdraw:", reply_markup=withdraw_currency_menu)
    await state.set_state(WithdrawState.selecting_currency)

@dispatcher.message(Command("stake"))
async def stake_command(message: types.Message, state: FSMContext):
    await message.reply("Choose a currency to stake:", reply_markup=stake_currency_menu)
    await state.set_state(StakeState.selecting_currency)

@dispatcher.message(Command("checkbalance"))
async def check_balance_command(message: types.Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if user:
        balance_usdt, balance_trx = user[2], user[3]
        await message.reply(f"Your balance: {balance_trx:,.2f} TRX and {balance_usdt:,.2f} USDT")
    else:
        await message.reply("User not found. Please start the bot.")

@dispatcher.message(Command("checkstaked"))
async def check_staked_command(message: types.Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        await message.reply("User not found.")
        return
    
    active_stakes = await get_active_stakes(user_id)
    if not active_stakes:
        await message.reply("You have no active stakes.")
        return
    
    response = "Your active stakes:\n"
    now = datetime.now()
    for stake in active_stakes:
        if len(stake) == 8:
            plan_id, amount, start_date, duration_days = stake[2], stake[3], stake[4], stake[5]
            currency = "USDT"
        else:
            plan_id, amount, currency, start_date, duration_days = stake[2], stake[3], stake[4], stake[5], stake[6]
        
        start_date = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S.%f')
        
        plan_desc = {
            1: "Starter 2%: Unlimited (From 10 {currency})",
            2: "Pro 3%: Unlimited (From 5,000 {currency})",
            3: "Elite 4%: Unlimited (From 20,000 {currency})",
            4: "40-Day Boost: 4% (40 days)",
            5: "60-Day Gain: 3% (60 days)",
            6: "100-Day Steady: 2% (100 days)"
        }[plan_id].format(currency=currency)
        
        response += f"- {plan_desc}: {amount:,.2f} {currency} (Started: {start_date})\n"
    await message.reply(response)

@dispatcher.message(Command("viewearnings"))
async def view_earnings_command(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if user:
        earnings_usdt, earnings_trx = await calculate_total_earnings(user_id)
        await message.reply(f"Your total earnings: {earnings_trx:,.2f} TRX and {earnings_usdt:,.2f} USDT", reply_markup=earnings_menu)
        await state.set_state(EarningsState.choosing_action)
    else:
        await message.reply("User not found.")

@dispatcher.message(Command("referral"))
async def referral_command(message: types.Message):
    user_id = message.from_user.id
    bot_info = await bot.get_me()
    referral_link = f"https://t.me/{bot_info.username}?start={user_id}"
    
    encoded_link = urllib.parse.quote(referral_link)
    share_button = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➦ Share", url=f"https://t.me/share/url?url={encoded_link}&text=Join this staking bot!")]
    ])
    
    await message.reply(f"Your referral link: {referral_link}", reply_markup=share_button)

# Handlerهای کیبورد (برای سازگاری با کد قبلی)
@dispatcher.message(F.text == "💰 Deposit")
async def deposit(message: types.Message, state: FSMContext):
    await deposit_command(message, state)

@dispatcher.message(F.text == "💳 Withdraw")
async def withdraw(message: types.Message, state: FSMContext):
    await withdraw_command(message, state)

@dispatcher.message(F.text == "💸 Stake")
async def stake(message: types.Message, state: FSMContext):
    await stake_command(message, state)

@dispatcher.message(F.text == "💼 Check Balance")
async def check_balance(message: types.Message):
    await check_balance_command(message)

@dispatcher.message(F.text == "📋 Check Staked")
async def check_staked(message: types.Message):
    await check_staked_command(message)

@dispatcher.message(F.text == "📈 View Earnings")
async def view_earnings(message: types.Message, state: FSMContext):
    await view_earnings_command(message, state)

@dispatcher.message(F.text == "👥 Referral Link")
async def referral_link(message: types.Message):
    await referral_command(message)

@dispatcher.message(DepositState.selecting_currency)
async def process_deposit_currency(message: types.Message, state: FSMContext):
    if message.text == "Back to Main Menu":
        await message.reply("Returning to main menu.", reply_markup=main_menu)
        await state.clear()
        return
    
    currency_map = {"Deposit USDT": "USDT", "Deposit TRX": "TRX"}
    if message.text not in currency_map:
        await message.reply("Please select a valid currency.", reply_markup=deposit_currency_menu)
        return
    
    currency = currency_map[message.text]
    await state.update_data(currency=currency)
    await message.reply(f"Please enter the amount of {currency} to deposit:", reply_markup=main_menu)
    await state.set_state(DepositState.waiting_for_amount)

@dispatcher.message(DepositState.waiting_for_amount)
async def process_deposit_amount(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    currency = data["currency"]
    
    try:
        amount = float(message.text)
        if amount <= 0:
            await message.reply("Please enter a positive amount.", reply_markup=main_menu)
            return
        
        # تولید آدرس واریز با NOWPayments
        address = await generate_payment_address(user_id, amount, currency)
        await save_deposit_address(user_id, currency, address)
        await message.reply(f"Please send {amount:.2f} {currency} to this address: {address}\nYour account will be credited automatically after confirmation.", reply_markup=main_menu)
        await state.clear()
    except ValueError:
        await message.reply("Invalid amount. Please enter a number.", reply_markup=main_menu)

@dispatcher.message(StakeState.selecting_currency)
async def process_stake_currency(message: types.Message, state: FSMContext):
    if message.text == "Back to Main Menu":
        await message.reply("Returning to main menu.", reply_markup=main_menu)
        await state.clear()
        return
    
    currency_map = {"Stake USDT": "USDT", "Stake TRX": "TRX"}
    if message.text not in currency_map:
        await message.reply("Please select a valid currency.", reply_markup=stake_currency_menu)
        return
    
    currency = currency_map[message.text]
    await state.update_data(currency=currency)
    await message.reply(f"Choose a staking plan for {currency}:", reply_markup=stake_plan_menu)
    await state.set_state(StakeState.selecting_plan)

@dispatcher.message(StakeState.selecting_plan, F.text.in_({"Starter 2%", "Pro 3%", "Elite 4%", "40-Day Boost", "60-Day Gain", "100-Day Steady", "Back to Main Menu"}))
async def process_plan_selection(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    currency = data["currency"]
    
    plan_descriptions = {
        "Starter 2%": f"Starter 2%: 2% Daily profit, unlimited duration (From 10 {currency})",
        "Pro 3%": f"Pro 3%: 3% Daily profit, unlimited duration (From 5,000 {currency})",
        "Elite 4%": f"Elite 4%: 4% Daily profit, unlimited duration (From 20,000 {currency})",
        "40-Day Boost": f"40-Day Boost: 4% Daily profit for 40 days (No amount limit)",
        "60-Day Gain": f"60-Day Gain: 3% Daily profit for 60 days (No amount limit)",
        "100-Day Steady": f"100-Day Steady: 2% Daily profit for 100 days (No amount limit)"
    }
    
    if message.text == "Back to Main Menu":
        await message.reply("Returning to main menu.", reply_markup=main_menu)
        await state.clear()
        return
    
    selected_plan = message.text
    if selected_plan in plan_descriptions:
        await message.reply(plan_descriptions[selected_plan])
        await message.reply(f"Please enter the amount of {currency} to stake:", reply_markup=stake_plan_menu)
        plan_id = {"Starter 2%": 1, "Pro 3%": 2, "Elite 4%": 3, "40-Day Boost": 4, "60-Day Gain": 5, "100-Day Steady": 6}[selected_plan]
        await state.update_data(plan_id=plan_id)
        await state.set_state(StakeState.waiting_for_amount)
    else:
        await message.reply("Please select a valid plan from the menu.", reply_markup=stake_plan_menu)

@dispatcher.message(StakeState.waiting_for_amount)
async def process_stake_amount(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    plan_id = data["plan_id"]
    currency = data["currency"]
    
    plan_names = {
        1: "Starter 2%",
        2: "Pro 3%",
        3: "Elite 4%",
        4: "40-Day Boost",
        5: "60-Day Gain",
        6: "100-Day Steady"
    }
    
    stake_menu_options = ["Starter 2%", "Pro 3%", "Elite 4%", "40-Day Boost", "60-Day Gain", "100-Day Steady", "Back to Main Menu"]
    if message.text in stake_menu_options:
        await process_plan_selection(message, state)
        return
    
    try:
        amount = float(message.text)
        if amount <= 0:
            await message.reply("Please enter a positive amount.", reply_markup=stake_plan_menu)
            return
        
        if plan_id == 1 and amount < 10:
            await message.reply(f"Amount must be at least 10 {currency} for Starter 2%.", reply_markup=stake_plan_menu)
            return
        elif plan_id == 2 and amount < 5000:
            await message.reply(f"Amount must be at least 5,000 {currency} for Pro 3%.", reply_markup=stake_plan_menu)
            return
        elif plan_id == 3 and amount < 20000:
            await message.reply(f"Amount must be at least 20,000 {currency} for Elite 4%.", reply_markup=stake_plan_menu)
            return
        
        user = await get_user(user_id)
        balance = user[2] if currency == "USDT" else user[3]
        if balance < amount:
            await message.reply(f"Insufficient {currency} balance.", reply_markup=stake_plan_menu)
            return
        
        duration_days = {1: None, 2: None, 3: None, 4: 40, 5: 60, 6: 100}[plan_id]
        await update_balance(user_id, -amount, currency)
        await add_stake(user_id, plan_id, amount, duration_days, currency)
        await add_transaction(user_id, f"stake_plan_{plan_id}", amount, currency)
        await message.reply(f"Staked {amount:,.2f} {currency} in {plan_names[plan_id]}. Check your stakes with 'Check Staked'.", reply_markup=main_menu)
        await state.clear()
    except ValueError:
        await message.reply("Invalid amount. Please enter a number.", reply_markup=stake_plan_menu)

@dispatcher.message(EarningsState.choosing_action)
async def process_earnings_action(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text == "Transfer to Balance":
        user = await get_user(user_id)
        earnings_usdt, earnings_trx = user[4], user[5]
        await message.reply(f"Please enter the amount you want to transfer to your balance:\nAvailable: {earnings_trx:,.2f} TRX and {earnings_usdt:,.2f} USDT\nSpecify currency (e.g., '10 TRX' or '5 USDT'):", reply_markup=earnings_menu)
        await state.set_state(EarningsState.entering_amount)
    elif message.text == "Back to Main Menu":
        await message.reply("Returning to main menu.", reply_markup=main_menu)
        await state.clear()
    else:
        await message.reply("Please choose an option from the menu.", reply_markup=earnings_menu)

@dispatcher.message(EarningsState.entering_amount)
async def process_transfer_amount(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text == "Back to Main Menu":
        await message.reply("Returning to main menu.", reply_markup=main_menu)
        await state.clear()
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2 or parts[1] not in ["TRX", "USDT"]:
            raise ValueError
        amount = float(parts[0])
        currency = parts[1]
        
        if amount <= 0:
            await message.reply("Please enter a positive amount.", reply_markup=earnings_menu)
            return
        success, response = await transfer_earnings_to_balance(user_id, amount, currency)
        await message.reply(response, reply_markup=earnings_menu)
    except ValueError:
        await message.reply("Invalid input. Please enter an amount and currency (e.g., '10 TRX' or '5 USDT').", reply_markup=earnings_menu)

@dispatcher.message(WithdrawState.selecting_currency)
async def process_withdraw_currency(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text == "Back to Main Menu":
        await message.reply("Returning to main menu.", reply_markup=main_menu)
        await state.clear()
        return
    
    currency_map = {"Withdraw USDT": "USDT", "Withdraw TRX": "TRX"}
    if message.text not in currency_map:
        await message.reply("Please select a valid currency.", reply_markup=withdraw_currency_menu)
        return
    
    currency = currency_map[message.text]
    await state.update_data(currency=currency)
    
    user = await get_user(user_id)
    earnings = user[4] if currency == "USDT" else user[5]
    await message.reply(f"Your available earnings for {currency}: {earnings:,.2f} {currency}. Enter the amount to withdraw:", reply_markup=main_menu)
    await state.set_state(WithdrawState.entering_amount)

@dispatcher.message(WithdrawState.entering_amount)
async def process_withdraw_amount(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    currency = data["currency"]
    
    min_withdraw = 20 if currency == "USDT" else 80
    fee = get_withdrawal_fee(currency)
    
    try:
        amount = float(message.text)
        if amount < min_withdraw:
            await message.reply(f"Amount must be at least {min_withdraw} {currency}.", reply_markup=main_menu)
            return
        
        total_amount = amount + fee
        user = await get_user(user_id)
        earnings = user[4] if currency == "USDT" else user[5]
        
        if earnings < total_amount:
            await message.reply(f"Insufficient {currency} earnings.", reply_markup=main_menu)
            return
        
        wallet_address = await get_wallet_address(user_id, currency)
        if not wallet_address:
            await message.reply(f"Please enter your {currency} wallet address:", reply_markup=main_menu)
            await state.set_state(WithdrawState.entering_new_address)
            return
        
        if await update_earnings(user_id, -total_amount, currency):
            await add_withdraw_request(user_id, amount, currency, fee, wallet_address)
            await message.reply(f"{amount:,.2f} {currency} has been deducted from your earnings (including {fee:.2f} {currency} network fee) and will be transferred to your wallet ({wallet_address}) within 24 hours after review.",
                               reply_markup=main_menu)
            await state.clear()
        else:
            await message.reply("Failed to process withdrawal. Try again.", reply_markup=main_menu)
    except ValueError:
        await message.reply("Invalid amount. Please enter a number.", reply_markup=main_menu)

@dispatcher.message(WithdrawState.entering_new_address)
async def process_new_address(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    currency = data["currency"]
    
    wallet_address = message.text
    await save_wallet_address(user_id, currency, wallet_address)
    await state.update_data(wallet_address=wallet_address)
    
    fee = get_withdrawal_fee(currency)
    min_withdraw = 20 if currency == "USDT" else 80
    await message.reply(f"Network fee for withdrawing {currency} is {fee:.2f} {currency}. Enter the amount to withdraw (minimum {min_withdraw} {currency}):",
                       reply_markup=main_menu)
    await state.set_state(WithdrawState.entering_amount)

# مدیریت پیام‌های نامعتبر
@dispatcher.message()
async def handle_invalid(message: types.Message):
    await message.reply("Please choose an option from the menu.", reply_markup=main_menu)

# تابع اصلی برای ربات
async def main():
    logging.info("Starting bot...")
    await initialize_database()
    asyncio.create_task(schedule_reports())
    await dispatcher.start_polling(bot)
    logging.info("Bot started polling.")

# تابع برای وب‌هوک
async def run_web():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8000)))
    await site.start()
    logging.info("Web server started.")

# اجرا با حلقه مشترک
if __name__ == "__main__":
    import aiohttp
    logging.info("Initializing app...")
    loop = asyncio.get_event_loop()
    try:
        loop.create_task(main())
        loop.create_task(run_web())
        loop.run_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down...")
    except Exception as e:
        logging.error(f"Error: {e}")