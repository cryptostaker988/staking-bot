import logging
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import F
from aiogram.filters import Command
import sqlite3
import os
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta
import urllib.parse
from aiohttp import web
import hmac
import hashlib
import json

# تعریف حالت‌ها
class EditEarningsState(StatesGroup):
    user_id = State()
    currency = State()
    amount = State()

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

class AdminState(StatesGroup):
    waiting_for_add_admin_id = State()
    waiting_for_remove_admin_id = State()
    waiting_for_edit_balance = State()
    waiting_for_delete_user = State()
    waiting_for_edit_stake_limit = State()
    waiting_for_edit_deposit_limit = State()

# تنظیمات اولیه
API_TOKEN = os.getenv("API_TOKEN", "8149978835:AAFcLTmqXz8o0VYu0zXiLQXElcsMI03J8CA")
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY", "4ECPB3V-PH6MKES-GZR79RZ-8HMMRSC")
IPN_SECRET = os.getenv("IPN_SECRET", "1N6xRI+EGoFRW+txIHd5O5srB9uq64ZT")
ADMIN_ID = 7509858897  # مقدار پیش‌فرض
logging.basicConfig(level=logging.INFO)
logging.info(f"Bot initialized with token: {API_TOKEN}")

app = web.Application()
bot = Bot(token=API_TOKEN)
dispatcher = Dispatcher()

db_lock = asyncio.Lock()

# اتصال به دیتابیس
async def db_connect():
    async with db_lock:
        for attempt in range(3):
            try:
                conn = sqlite3.connect("/opt/render/project/db/staking_bot.db", timeout=30)
                return conn
            except sqlite3.OperationalError as e:
                logging.error(f"Database connection attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(1)
                else:
                    raise

# مقداردهی اولیه دیتابیس
async def initialize_database():
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                            user_id INTEGER PRIMARY KEY,
                            username TEXT,
                            balance_usdt REAL DEFAULT 0,
                            balance_trx REAL DEFAULT 0,
                            balance_bnb REAL DEFAULT 0,
                            balance_doge REAL DEFAULT 0,
                            balance_ton REAL DEFAULT 0,
                            earnings_usdt REAL DEFAULT 0,
                            earnings_trx REAL DEFAULT 0,
                            earnings_bnb REAL DEFAULT 0,
                            earnings_doge REAL DEFAULT 0,
                            earnings_ton REAL DEFAULT 0,
                            last_earning_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            referrer_id INTEGER DEFAULT NULL
                        )''')
        
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN balance_usdt REAL DEFAULT 0")
            cursor.execute("ALTER TABLE users ADD COLUMN balance_trx REAL DEFAULT 0")
            cursor.execute("ALTER TABLE users ADD COLUMN balance_bnb REAL DEFAULT 0")
            cursor.execute("ALTER TABLE users ADD COLUMN balance_doge REAL DEFAULT 0")
            cursor.execute("ALTER TABLE users ADD COLUMN balance_ton REAL DEFAULT 0")
            cursor.execute("ALTER TABLE users ADD COLUMN earnings_usdt REAL DEFAULT 0")
            cursor.execute("ALTER TABLE users ADD COLUMN earnings_trx REAL DEFAULT 0")
            cursor.execute("ALTER TABLE users ADD COLUMN earnings_bnb REAL DEFAULT 0")
            cursor.execute("ALTER TABLE users ADD COLUMN earnings_doge REAL DEFAULT 0")
            cursor.execute("ALTER TABLE users ADD COLUMN earnings_ton REAL DEFAULT 0")
            cursor.execute("ALTER TABLE users ADD COLUMN last_earning_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            cursor.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT NULL")
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
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS admins (
                            user_id INTEGER PRIMARY KEY
                          )''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS limits (
                            currency TEXT,
                            plan_id INTEGER,
                            min_amount REAL,
                            type TEXT,
                            PRIMARY KEY (currency, plan_id, type)
                        )''')
        
        cursor.execute("SELECT COUNT(*) FROM limits WHERE type = 'deposit'")
        if cursor.fetchone()[0] == 0:
            initial_deposit_limits = [
                ("USDT", 0, 20.0), ("TRX", 0, 40.0), ("BNB", 0, 0.02), ("DOGE", 0, 150.0), ("TON", 0, 8.0)
            ]
            cursor.executemany("INSERT INTO limits (currency, plan_id, min_amount, type) VALUES (?, ?, ?, 'deposit')", initial_deposit_limits)
            logging.info("Initialized default deposit limits.")
        
        cursor.execute("SELECT COUNT(*) FROM limits WHERE type = 'stake'")
        if cursor.fetchone()[0] == 0:
            initial_stake_limits = [
                ("USDT", 1, 50), ("USDT", 2, 5000), ("USDT", 3, 20000), ("USDT", 4, 0), ("USDT", 5, 0), ("USDT", 6, 0),
                ("TRX", 1, 200), ("TRX", 2, 20000), ("TRX", 3, 80000), ("TRX", 4, 0), ("TRX", 5, 0), ("TRX", 6, 0),
                ("BNB", 1, 0.1), ("BNB", 2, 10), ("BNB", 3, 35), ("BNB", 4, 0), ("BNB", 5, 0), ("BNB", 6, 0),
                ("DOGE", 1, 200), ("DOGE", 2, 25000), ("DOGE", 3, 100000), ("DOGE", 4, 0), ("DOGE", 5, 0), ("DOGE", 6, 0),
                ("TON", 1, 20), ("TON", 2, 1500), ("TON", 3, 6000), ("TON", 4, 0), ("TON", 5, 0), ("TON", 6, 0)
            ]
            cursor.executemany("INSERT INTO limits (currency, plan_id, min_amount, type) VALUES (?, ?, ?, 'stake')", initial_stake_limits)
            logging.info("Initialized default stake limits.")
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS processed_payments (
                            payment_id INTEGER PRIMARY KEY,
                            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )''')
        
        conn.commit()
        conn.close()
        logging.info("Database initialized successfully.")

async def add_user(user_id, username, referrer_id=None):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (int(user_id),))
        user = cursor.fetchone()
        if user:
            cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, int(user_id)))
            logging.info(f"Updated user {user_id} with username {username}")
        else:
            if referrer_id and isinstance(referrer_id, int):
                logging.info(f"Adding new user {user_id} with referrer_id {referrer_id}")
                cursor.execute("INSERT INTO users (user_id, username, last_earning_update, referrer_id) VALUES (?, ?, ?, ?)", 
                              (int(user_id), username, datetime.now(), referrer_id))
            else:
                logging.info(f"Adding new user {user_id} with no referrer")
                cursor.execute("INSERT INTO users (user_id, username, last_earning_update) VALUES (?, ?, ?)", 
                              (int(user_id), username, datetime.now()))
        conn.commit()
        conn.close()

async def get_user(user_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (int(user_id),))
        user = cursor.fetchone()
        conn.close()
        return user
    return None

async def is_admin(user_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM admins WHERE user_id = ?", (int(user_id),))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    return False

async def get_user_stakes(user_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM stakes WHERE user_id = ?", (int(user_id),))
        stakes = cursor.fetchall()
        conn.close()
        return stakes
    return []

async def get_active_stakes(user_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM stakes WHERE user_id = ?", (int(user_id),))
        all_stakes = cursor.fetchall()
        conn.close()
        
        active_stakes = []
        now = datetime.now()
        for stake in all_stakes:
            if len(stake) == 8:
                stake_id, _, plan_id, amount, start_date, duration_days, last_update, is_expired = stake
                currency = "USDT"
            else:
                stake_id, _, plan_id, amount, currency, start_date, duration_days, last_update, is_expired = stake
            
            start_date = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S.%f')
            days_passed = (now - start_date).total_seconds() / (24 * 3600)
            
            if (duration_days is None or days_passed < duration_days) and is_expired == 0:
                active_stakes.append(stake)
        
        return active_stakes
    return []

async def update_balance(user_id, amount, currency):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT balance_usdt, balance_trx, balance_bnb, balance_doge, balance_ton FROM users WHERE user_id = ?", (int(user_id),))
        user = cursor.fetchone()
        if user:
            balance_usdt, balance_trx, balance_bnb, balance_doge, balance_ton = user
            if currency == "USDT":
                new_balance = balance_usdt + amount
                if new_balance < 0:
                    conn.close()
                    return False
                cursor.execute("UPDATE users SET balance_usdt = ? WHERE user_id = ?", (new_balance, int(user_id)))
            elif currency == "TRX":
                new_balance = balance_trx + amount
                if new_balance < 0:
                    conn.close()
                    return False
                cursor.execute("UPDATE users SET balance_trx = ? WHERE user_id = ?", (new_balance, int(user_id)))
            elif currency == "BNB":
                new_balance = balance_bnb + amount
                if new_balance < 0:
                    conn.close()
                    return False
                cursor.execute("UPDATE users SET balance_bnb = ? WHERE user_id = ?", (new_balance, int(user_id)))
            elif currency == "DOGE":
                new_balance = balance_doge + amount
                if new_balance < 0:
                    conn.close()
                    return False
                cursor.execute("UPDATE users SET balance_doge = ? WHERE user_id = ?", (new_balance, int(user_id)))
            elif currency == "TON":
                new_balance = balance_ton + amount
                if new_balance < 0:
                    conn.close()
                    return False
                cursor.execute("UPDATE users SET balance_ton = ? WHERE user_id = ?", (new_balance, int(user_id)))
            conn.commit()
        else:
            try:
                if currency == "USDT":
                    cursor.execute("INSERT INTO users (user_id, balance_usdt) VALUES (?, ?)", (int(user_id), amount))
                elif currency == "TRX":
                    cursor.execute("INSERT INTO users (user_id, balance_trx) VALUES (?, ?)", (int(user_id), amount))
                elif currency == "BNB":
                    cursor.execute("INSERT INTO users (user_id, balance_bnb) VALUES (?, ?)", (int(user_id), amount))
                elif currency == "DOGE":
                    cursor.execute("INSERT INTO users (user_id, balance_doge) VALUES (?, ?)", (int(user_id), amount))
                elif currency == "TON":
                    cursor.execute("INSERT INTO users (user_id, balance_ton) VALUES (?, ?)", (int(user_id), amount))
                conn.commit()
            except sqlite3.IntegrityError as e:
                logging.error(f"Failed to insert new user {user_id} for {currency}: {e}")
                conn.close()
                return False
        conn.close()
        return True
    return False

async def update_earnings(user_id, earnings_change, currency):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT earnings_usdt, earnings_trx, earnings_bnb, earnings_doge, earnings_ton FROM users WHERE user_id = ?", (int(user_id),))
        user = cursor.fetchone()
        if user:
            earnings_usdt, earnings_trx, earnings_bnb, earnings_doge, earnings_ton = user
            if currency == "USDT":
                new_earnings = earnings_usdt + earnings_change
                if new_earnings < 0:
                    conn.close()
                    return False
                cursor.execute("UPDATE users SET earnings_usdt = ?, last_earning_update = ? WHERE user_id = ?", 
                              (new_earnings, datetime.now(), int(user_id)))
            elif currency == "TRX":
                new_earnings = earnings_trx + earnings_change
                if new_earnings < 0:
                    conn.close()
                    return False
                cursor.execute("UPDATE users SET earnings_trx = ?, last_earning_update = ? WHERE user_id = ?", 
                              (new_earnings, datetime.now(), int(user_id)))
            elif currency == "BNB":
                new_earnings = earnings_bnb + earnings_change
                if new_earnings < 0:
                    conn.close()
                    return False
                cursor.execute("UPDATE users SET earnings_bnb = ?, last_earning_update = ? WHERE user_id = ?", 
                              (new_earnings, datetime.now(), int(user_id)))
            elif currency == "DOGE":
                new_earnings = earnings_doge + earnings_change
                if new_earnings < 0:
                    conn.close()
                    return False
                cursor.execute("UPDATE users SET earnings_doge = ?, last_earning_update = ? WHERE user_id = ?", 
                              (new_earnings, datetime.now(), int(user_id)))
            elif currency == "TON":
                new_earnings = earnings_ton + earnings_change
                if new_earnings < 0:
                    conn.close()
                    return False
                cursor.execute("UPDATE users SET earnings_ton = ?, last_earning_update = ? WHERE user_id = ?", 
                              (new_earnings, datetime.now(), int(user_id)))
            conn.commit()
        conn.close()
        return True
    return False

async def add_stake(user_id, plan_id, amount, duration_days, currency):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO stakes (user_id, plan_id, amount, currency, start_date, duration_days, last_earning_update, is_expired) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (int(user_id), plan_id, amount, currency, datetime.now(), duration_days, datetime.now(), 0))
        conn.commit()
        conn.close()
        logging.info(f"Stake added: user_id={user_id}, plan_id={plan_id}, amount={amount}, currency={currency}, duration_days={duration_days}")
        return True
    return False

async def calculate_total_earnings(user_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT earnings_usdt, earnings_trx, earnings_bnb, earnings_doge, earnings_ton FROM users WHERE user_id = ?", (int(user_id),))
        earnings = cursor.fetchone()
        past_earnings_usdt, past_earnings_trx, past_earnings_bnb, past_earnings_doge, past_earnings_ton = earnings if earnings else (0, 0, 0, 0, 0)
        
        stakes = await get_user_stakes(user_id)
        total_new_earnings_usdt = 0
        total_new_earnings_trx = 0
        total_new_earnings_bnb = 0
        total_new_earnings_doge = 0
        total_new_earnings_ton = 0
        now = datetime.now()
        
        for stake in stakes:
            if len(stake) == 8:
                stake_id, _, plan_id, amount, start_date, duration_days, last_update, is_expired = stake
                currency = "USDT"
            else:
                stake_id, _, plan_id, amount, currency, start_date, duration_days, last_update, is_expired = stake
            
            start_date = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S.%f')
            last_update = datetime.strptime(last_update, '%Y-%m-%d %H:%M:%S.%f') if isinstance(last_update, str) else last_update
            days_passed = (now - start_date).total_seconds() / (24 * 3600)
            days_since_last = (now - last_update).total_seconds() / (24 * 3600)
            
            profit_rate = {1: 0.02, 2: 0.03, 3: 0.04, 4: 0.04, 5: 0.03, 6: 0.025}[plan_id]
            
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
                    elif currency == "BNB":
                        total_new_earnings_bnb += new_earnings
                    elif currency == "DOGE":
                        total_new_earnings_doge += new_earnings
                    elif currency == "TON":
                        total_new_earnings_ton += new_earnings
                    cursor.execute("UPDATE stakes SET last_earning_update = ? WHERE id = ?", (now, stake_id))
            elif days_passed >= duration_days and is_expired == 0:
                stake_earnings = amount * profit_rate * duration_days
                if currency == "USDT":
                    total_new_earnings_usdt += stake_earnings
                elif currency == "TRX":
                    total_new_earnings_trx += stake_earnings
                elif currency == "BNB":
                    total_new_earnings_bnb += stake_earnings
                elif currency == "DOGE":
                    total_new_earnings_doge += stake_earnings
                elif currency == "TON":
                    total_new_earnings_ton += stake_earnings
                cursor.execute("UPDATE stakes SET last_earning_update = ?, is_expired = 1 WHERE id = ?", (now, stake_id))
        
        if total_new_earnings_usdt > 0:
            await update_earnings(user_id, total_new_earnings_usdt, "USDT")
        if total_new_earnings_trx > 0:
            await update_earnings(user_id, total_new_earnings_trx, "TRX")
        if total_new_earnings_bnb > 0:
            await update_earnings(user_id, total_new_earnings_bnb, "BNB")
        if total_new_earnings_doge > 0:
            await update_earnings(user_id, total_new_earnings_doge, "DOGE")
        if total_new_earnings_ton > 0:
            await update_earnings(user_id, total_new_earnings_ton, "TON")
        
        cursor.execute("SELECT earnings_usdt, earnings_trx, earnings_bnb, earnings_doge, earnings_ton FROM users WHERE user_id = ?", (int(user_id),))
        earnings_usdt, earnings_trx, earnings_bnb, earnings_doge, earnings_ton = cursor.fetchone()
        conn.commit()
        conn.close()
        return earnings_usdt, earnings_trx, earnings_bnb, earnings_doge, earnings_ton
    return 0, 0, 0, 0, 0

async def add_transaction(user_id, transaction_type, amount, currency):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO transactions (user_id, transaction_type, amount, currency) VALUES (?, ?, ?, ?)", 
                      (int(user_id), transaction_type, amount, currency))
        conn.commit()
        conn.close()
        logging.info(f"Transaction added: {transaction_type} {amount} {currency} for user {user_id}")

async def transfer_earnings_to_balance(user_id, amount, currency):
    user = await get_user(user_id)
    if user:
        earnings_usdt, earnings_trx, earnings_bnb, earnings_doge, earnings_ton = user[5], user[6], user[7], user[8], user[9]
        earnings = {
            "USDT": earnings_usdt,
            "TRX": earnings_trx,
            "BNB": earnings_bnb,
            "DOGE": earnings_doge,
            "TON": earnings_ton
        }[currency]
        if amount > 0 and amount <= earnings:
            if await update_balance(user_id, amount, currency) and await update_earnings(user_id, -amount, currency):
                await add_transaction(user_id, "earnings_transfer", amount, currency)
                user = await get_user(user_id)
                new_balance = {
                    "USDT": user[2],
                    "TRX": user[3],
                    "BNB": user[4],
                    "DOGE": user[5],
                    "TON": user[6]
                }[currency]
                if currency == "BNB":
                    return True, f"{amount:.6f} {currency} has been transferred to your balance. New {currency} balance: {new_balance:.6f} {currency}"
                else:
                    return True, f"{amount:.2f} {currency} has been transferred to your balance. New {currency} balance: {new_balance:.2f} {currency}"
            else:
                return False, "Failed to transfer earnings. Try again."
        else:
            if currency == "BNB":
                return False, f"You don’t have enough earnings. Your current {currency} earnings: {earnings:.6f} {currency}"
            else:
                return False, f"You don’t have enough earnings. Your current {currency} earnings: {earnings:.2f} {currency}"
    return False, "User not found."

async def get_wallet_address(user_id, currency):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT wallet_address FROM wallets WHERE user_id = ? AND currency = ?", (int(user_id), currency))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    return None

async def get_deposit_address(user_id, currency):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT deposit_address FROM wallets WHERE user_id = ? AND currency = ?", (int(user_id), currency))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    return None

async def save_wallet_address(user_id, currency, wallet_address):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO wallets (user_id, currency, wallet_address) VALUES (?, ?, ?)",
                      (int(user_id), currency, wallet_address))
        conn.commit()
        conn.close()

async def save_deposit_address(user_id, currency, address):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE wallets SET deposit_address = ? WHERE user_id = ? AND currency = ?",
                      (address, int(user_id), currency))
        if cursor.rowcount == 0:
            cursor.execute("INSERT INTO wallets (user_id, currency, deposit_address) VALUES (?, ?, ?)",
                          (int(user_id), currency, address))
        conn.commit()
        conn.close()

async def generate_payment_address(user_id, amount, currency):
    headers = {"x-api-key": NOWPAYMENTS_API_KEY}
    pay_currency_map = {
        "USDT": "usdttrc20",
        "TRX": "trx",
        "BNB": "bnbbsc",
        "DOGE": "doge",
        "TON": "ton"
    }
    pay_currency = pay_currency_map[currency]
    payload = {
        "price_amount": amount,
        "price_currency": pay_currency,
        "pay_currency": pay_currency,
        "order_id": str(user_id),
        "ipn_callback_url": "https://new-staking-bot.onrender.com/webhook"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://api.nowpayments.io/v1/payment", json=payload, headers=headers) as resp:
            status = resp.status
            data = await resp.json()
            logging.info(f"NOWPayments request: status={status}, payload={payload}, response={data}")
            if "pay_address" in data:
                logging.info(f"Successfully generated address for {currency}: {data['pay_address']}")
                return data['pay_address']
            else:
                error_msg = data.get("message", "Unknown error")
                logging.error(f"Failed to get pay_address: {error_msg}, status={status}")
                return None

async def get_min_limit(currency, plan_id=0, limit_type="deposit"):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT min_amount FROM limits WHERE currency = ? AND plan_id = ? AND type = ?", (currency, plan_id, limit_type))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else 0
    return 0

async def update_min_limit(currency, plan_id, min_amount, limit_type):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO limits (currency, plan_id, min_amount, type) VALUES (?, ?, ?, ?)",
                      (currency, plan_id, min_amount, limit_type))
        conn.commit()
        conn.close()
        return True
    return False

async def check_last_withdrawal(user_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, request_time FROM withdraw_requests WHERE user_id = ? ORDER BY request_time DESC LIMIT 1", (int(user_id),))
        result = cursor.fetchone()
        conn.close()
        if result:
            status, request_time = result
            request_time = datetime.strptime(request_time, '%Y-%m-%d %H:%M:%S.%f')
            return status, request_time
        return None, None

async def add_withdraw_request(user_id, amount, currency, fee, wallet_address):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO withdraw_requests (user_id, amount, currency, fee, wallet_address, status, request_time) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (int(user_id), amount, currency, fee, wallet_address, "Pending", datetime.now()))
        conn.commit()
        conn.close()
        logging.info(f"Withdrawal request added: user_id={user_id}, amount={amount}, currency={currency}")

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

async def complete_withdrawal(request_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE withdraw_requests SET status = 'Completed' WHERE id = ?", (request_id,))
        conn.commit()
        conn.close()

async def reject_withdrawal(request_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE withdraw_requests SET status = 'Rejected' WHERE id = ?", (request_id,))
        conn.commit()
        conn.close()

async def get_withdrawal_details(request_id):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, amount, currency, wallet_address FROM withdraw_requests WHERE id = ?", (request_id,))
        result = cursor.fetchone()
        conn.close()
        return result
    return None

def get_withdrawal_fee(currency):
    return {
        "USDT": 3.0,
        "TRX": 1.1,
        "BNB": 0.002,
        "DOGE": 1.0,
        "TON": 0.1
    }[currency]

async def get_min_deposit(currency):
    return await get_min_limit(currency, 0, "deposit")

async def get_min_withdrawal(currency):
    return await get_min_limit(currency, 0, "deposit")

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
        if req[3] == "BNB":
            report += f"ID: {req[0]} | User: {req[1]} | Amount: {req[2]:.6f} {req[3]} | Fee: {req[4]:.6f} {req[3]} | Address: {req[5]} | Time: {req[7]}\n"
        else:
            report += f"ID: {req[0]} | User: {req[1]} | Amount: {req[2]:.2f} {req[3]} | Fee: {req[4]:.2f} {req[3]} | Address: {req[5]} | Time: {req[7]}\n"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"Complete ID {req[0]}", callback_data=f"complete_{req[0]}"),
            InlineKeyboardButton(text=f"Reject ID {req[0]}", callback_data=f"reject_{req[0]}")
        ])
    
    await bot.send_message(ADMIN_ID, report, reply_markup=keyboard)

async def schedule_reports():
    while True:
        await send_withdrawal_report()
        await asyncio.sleep(43200)

async def handle_webhook(request):
    signature = request.headers.get("x-nowpayments-sig")
    body = await request.text()
    data = json.loads(body)

    computed_sig = hmac.new(IPN_SECRET.encode(), body.encode(), hashlib.sha512).hexdigest()
    if computed_sig != signature:
        logging.error(f"Invalid signature: received={signature}, computed={computed_sig}")
        return web.Response(text="Invalid signature", status=403)

    logging.info(f"Webhook received: {data}")

    status = data.get("payment_status")
    if status not in ["confirmed", "finished", "partially_paid"]:
        logging.info(f"Payment status '{status}' not confirmed yet, skipping.")
        return web.Response(text="Success")

    payment_id = data.get("payment_id")
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT payment_id FROM processed_payments WHERE payment_id = ?", (payment_id,))
        if cursor.fetchone():
            logging.info(f"Payment {payment_id} already processed, skipping.")
            conn.close()
            return web.Response(text="Success")
        conn.close()

    user_id = int(data.get("order_id"))
    amount = data.get("actually_paid") or data.get("pay_amount") or data.get("price_amount")
    if amount is None:
        logging.error("No valid amount found in webhook data.")
        return web.Response(text="No amount provided", status=400)
    
    amount = float(amount)
    currency = data.get("pay_currency", "").upper()
    if currency == "USDTTRC20":
        currency = "USDT"
    elif currency == "BNBBSC":
        currency = "BNB"

    min_deposit = await get_min_deposit(currency)
    logging.info(f"Checking deposit: amount={amount}, min_deposit={min_deposit}, condition={amount >= min_deposit}")

    if amount < min_deposit:
        credited_amount = amount * 0.9
        logging.info(f"Deposit below minimum: crediting {credited_amount} {currency} to user {user_id}")
        await update_balance(user_id, credited_amount, currency)
        await add_transaction(user_id, "deposit", credited_amount, currency)
        if currency == "BNB":
            await bot.send_message(user_id, f"Your deposit of {str(amount).rstrip('0').rstrip('.')} {currency} was below the minimum ({str(min_deposit).rstrip('0').rstrip('.')}). Due to a 10% fee, {str(credited_amount).rstrip('0').rstrip('.')} {currency} has been credited!")
        else:
            await bot.send_message(user_id, f"Your deposit of {amount:.2f} {currency} was below the minimum ({min_deposit:.2f} {currency}). Due to a 10% fee, {credited_amount:.2f} {currency} has been credited!")
        user = await get_user(user_id)
        if user and user[13] and isinstance(user[13], int):
            referrer_id = user[13]
            logging.info(f"No bonus for referrer {referrer_id} due to below-minimum deposit")
            if currency == "BNB":
                await bot.send_message(referrer_id, f"Because your referral (user {user_id}) deposited {str(amount).rstrip('0').rstrip('.')} {currency}, which is less than the minimum ({str(min_deposit).rstrip('0').rstrip('.')}), no referral bonus was credited.")
            else:
                await bot.send_message(referrer_id, f"Because your referral (user {user_id}) deposited {amount:.2f} {currency}, which is less than the minimum ({min_deposit:.2f} {currency}), no referral bonus was credited.")
    else:
        credited_amount = amount
        logging.info(f"Crediting deposit: {credited_amount} {currency} to user {user_id}")
        await update_balance(user_id, credited_amount, currency)
        await add_transaction(user_id, "deposit", credited_amount, currency)
        if currency == "BNB":
            await bot.send_message(user_id, f"Your deposit of {str(amount).rstrip('0').rstrip('.')} {currency} has been credited!")
        else:
            await bot.send_message(user_id, f"Your deposit of {amount:.2f} {currency} has been credited!")
        user = await get_user(user_id)
        if user and user[13] and isinstance(user[13], int):
            referrer_id = user[13]
            bonus_amount = credited_amount * 0.05
            logging.info(f"Crediting referral bonus: {bonus_amount:.5f} {currency} to referrer {referrer_id}")
            success = await update_balance(referrer_id, bonus_amount, currency)
            if success:
                await add_transaction(referrer_id, "referral_bonus", bonus_amount, currency)
                if currency == "BNB":
                    await bot.send_message(referrer_id, f"Your balance has been increased by {bonus_amount:.5f} {currency} as a referral bonus from user {user_id}.")
                else:
                    await bot.send_message(referrer_id, f"Your balance has been increased by {bonus_amount:.2f} {currency} as a referral bonus from user {user_id}.")
            else:
                logging.error(f"Failed to credit bonus {bonus_amount:.5f} {currency} to referrer {referrer_id}")
        else:
            logging.warning(f"No valid referrer_id for user {user_id}: {user[13] if user else 'No user'}")

    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO processed_payments (payment_id) VALUES (?)", (payment_id,))
        conn.commit()
        conn.close()

    return web.Response(text="Success")

app.router.add_post('/webhook', handle_webhook)

async def handle_telegram_webhook(request):
    update = types.Update(**(await request.json()))
    await dispatcher.feed_update(bot, update)
    return web.Response(text="OK")

app.router.add_post('/telegram-webhook', handle_telegram_webhook)

# منوها
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💰 Deposit"), KeyboardButton(text="💳 Withdraw")],
        [KeyboardButton(text="💸 Stake"), KeyboardButton(text="💼 Check Balance")],
        [KeyboardButton(text="📋 Check Staked"), KeyboardButton(text="📈 View Earnings")],
        [KeyboardButton(text="👥 Referral Link"), KeyboardButton(text="❓ Guide")]
    ],
    resize_keyboard=True
)

deposit_currency_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Deposit USDT"), KeyboardButton(text="Deposit TRX")],
        [KeyboardButton(text="Deposit BNB"), KeyboardButton(text="Deposit DOGE")],
        [KeyboardButton(text="Deposit TON"), KeyboardButton(text="Back to Main Menu")]
    ],
    resize_keyboard=True
)

stake_currency_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Stake USDT"), KeyboardButton(text="Stake TRX")],
        [KeyboardButton(text="Stake BNB"), KeyboardButton(text="Stake DOGE")],
        [KeyboardButton(text="Stake TON"), KeyboardButton(text="Back to Main Menu")]
    ],
    resize_keyboard=True
)

stake_plan_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Starter 2% Forever"), KeyboardButton(text="Pro 3% Forever")],
        [KeyboardButton(text="Elite 4% Forever"), KeyboardButton(text="40-Day 4% Daily")],
        [KeyboardButton(text="60-Day 3% Daily"), KeyboardButton(text="100-Day 2.5% Daily")],
        [KeyboardButton(text="Back to Main Menu")]
    ],
    resize_keyboard=True
)

withdraw_currency_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Withdraw USDT"), KeyboardButton(text="Withdraw TRX")],
        [KeyboardButton(text="Withdraw BNB"), KeyboardButton(text="Withdraw DOGE")],
        [KeyboardButton(text="Withdraw TON"), KeyboardButton(text="Back to Main Menu")]
    ],
    resize_keyboard=True
)

address_confirmation_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Yes"), KeyboardButton(text="Change Address")],
        [KeyboardButton(text="Back to Main Menu")]
    ],
    resize_keyboard=True
)

earnings_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Back to Main Menu"), KeyboardButton(text="Transfer to Balance")]
    ],
    resize_keyboard=True
)

async def get_admin_menu(username):
    admin_menu = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="View Users", callback_data="view_users"),
         InlineKeyboardButton(text="Edit Balance", callback_data="edit_balance")],
        [InlineKeyboardButton(text="Delete User", callback_data="delete_user"),
         InlineKeyboardButton(text="Bot Stats", callback_data="stats")],
        [InlineKeyboardButton(text="Edit Stake Limits", callback_data="edit_stake_limits"),
         InlineKeyboardButton(text="Edit Deposit Limits", callback_data="edit_deposit_limits")],
        [InlineKeyboardButton(text="Edit Earnings", callback_data="admin_edit_earnings")]
    ])
    if username.lower() in ["coinstakebot_admin", "tyhi87655"]:
        admin_menu.inline_keyboard.append([
            InlineKeyboardButton(text="Add Admin", callback_data="add_admin"),
            InlineKeyboardButton(text="Remove Admin", callback_data="remove_admin")
        ])
    return admin_menu

@dispatcher.message(Command("start"))
async def send_welcome(message: types.Message):
    global ADMIN_ID
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    command_parts = message.text.split()
    referrer_id = int(command_parts[1]) if len(command_parts) > 1 and command_parts[1].isdigit() else None
    
    await add_user(user_id, username, referrer_id)
    if username.lower() in ["coinstakebot_admin", "tyhi87655"]:
        ADMIN_ID = user_id
        logging.info(f"Admin ID set to: {ADMIN_ID}")
        conn = await db_connect()
        if conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
            conn.commit()
            conn.close()
    await message.reply("Welcome to CoinStake! For each deposit by your referrals, 5% of their deposit will be added to your balance as a bonus. Choose an option:", reply_markup=main_menu)

@dispatcher.message(Command("admin"))
async def admin_panel(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    
    if not await is_admin(user_id):
        await message.reply("You are not an admin!")
        return
    
    admin_menu = await get_admin_menu(username)
    await message.reply("Admin Panel:", reply_markup=admin_menu)

@dispatcher.callback_query(F.callback_data == "admin_edit_earnings")
async def process_edit_earnings(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Enter the user ID to edit earnings:")
    await EditEarningsState.user_id.set()
    await callback.answer()

@dispatcher.message(EditEarningsState.user_id)
async def process_user_id(message: types.Message, state: FSMContext):
    user_id = message.text
    try:
        user_id = int(user_id)
        await state.update_data(user_id=user_id)
        
        currency_menu = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="BNB", callback_data="currency_BNB"),
             InlineKeyboardButton(text="USDT", callback_data="currency_USDT")],
            [InlineKeyboardButton(text="TRX", callback_data="currency_TRX"),
             InlineKeyboardButton(text="DOGE", callback_data="currency_DOGE")],
            [InlineKeyboardButton(text="TON", callback_data="currency_TON"),
             InlineKeyboardButton(text="Back", callback_data="cancel_edit")]
        ])
        await message.reply("Select the currency for earnings:", reply_markup=currency_menu)
    except ValueError:
        await message.reply("Please enter a valid user ID (numbers only)!")
        await state.clear()  # تغییر از finish به clear

@dispatcher.callback_query(F.callback_data.startswith("currency_"))
async def process_currency_selection(callback: types.CallbackQuery, state: FSMContext):
    currency = callback.data.split("_")[1]
    await state.update_data(currency=currency)
    await callback.message.edit_text(f"Enter the new earnings amount for {currency} (e.g., 0.01):")
    await EditEarningsState.amount.set()
    await callback.answer()

@dispatcher.callback_query(F.callback_data == "cancel_edit")
async def cancel_edit(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    admin_menu = await get_admin_menu(callback.from_user.username or "Unknown")
    await callback.message.edit_text("Admin Panel:", reply_markup=admin_menu)
    await callback.answer()

@dispatcher.message(EditEarningsState.amount)
async def process_earnings_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        data = await state.get_data()
        user_id = data["user_id"]
        currency = data["currency"].lower()
        
        conn = await db_connect()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            await message.reply(f"No user found with ID {user_id}!")
            await state.clear()  # تغییر از finish به clear
            conn.close()
            return
        
        cursor.execute(f"UPDATE users SET earnings_{currency} = ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        conn.close()
        
        await message.reply(f"Earnings for user {user_id} updated to {amount} {currency.upper()}!")
        await state.clear()  # تغییر از finish به clear
        admin_menu = await get_admin_menu(message.from_user.username or "Unknown")
        await message.reply("Admin Panel:", reply_markup=admin_menu)
    except ValueError:
        await message.reply("Please enter a valid number (e.g., 0.01)!")
        await state.clear()  # تغییر از finish به clear

@dispatcher.message(F.text == "💰 Deposit")
async def deposit_menu(message: types.Message):
    await message.reply("Select a currency to deposit:", reply_markup=deposit_currency_menu)

@dispatcher.message(F.text.startswith("Deposit "))
async def process_deposit_currency(message: types.Message, state: FSMContext):
    currency = message.text.split()[1]
    await state.update_data(currency=currency)
    min_deposit = await get_min_deposit(currency)
    if currency == "BNB":
        await message.reply(f"Enter the amount to deposit in {currency} (minimum {str(min_deposit).rstrip('0').rstrip('.')} {currency}):")
    else:
        await message.reply(f"Enter the amount to deposit in {currency} (minimum {min_deposit:.2f} {currency}):")
    await DepositState.waiting_for_amount.set()

@dispatcher.message(DepositState.waiting_for_amount)
async def process_deposit_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        data = await state.get_data()
        currency = data["currency"]
        min_deposit = await get_min_deposit(currency)
        if amount < min_deposit:
            if currency == "BNB":
                await message.reply(f"Amount must be at least {str(min_deposit).rstrip('0').rstrip('.')} {currency}!")
            else:
                await message.reply(f"Amount must be at least {min_deposit:.2f} {currency}!")
            await state.clear()  # تغییر از finish به clear
            return
        
        user_id = message.from_user.id
        address = await generate_payment_address(user_id, amount, currency)
        if address:
            await save_deposit_address(user_id, currency, address)
            if currency == "BNB":
                await message.reply(f"Please send {str(amount).rstrip('0').rstrip('.')} {currency} to this address:\n`{address}`\n\nFunds will be credited after confirmation.", parse_mode="Markdown")
            else:
                await message.reply(f"Please send {amount:.2f} {currency} to this address:\n`{address}`\n\nFunds will be credited after confirmation.", parse_mode="Markdown")
        else:
            await message.reply("Failed to generate deposit address. Please try again later.")
        await state.clear()  # تغییر از finish به clear
    except ValueError:
        await message.reply("Please enter a valid number (e.g., 10.5)!")
        await state.clear()  # تغییر از finish به clear

@dispatcher.message(F.text == "💸 Stake")
async def stake_menu(message: types.Message):
    await message.reply("Select a currency to stake:", reply_markup=stake_currency_menu)

@dispatcher.message(F.text.startswith("Stake "))
async def process_stake_currency(message: types.Message, state: FSMContext):
    currency = message.text.split()[1]
    await state.update_data(currency=currency)
    await message.reply("Choose a staking plan:", reply_markup=stake_plan_menu)
    await StakeState.selecting_plan.set()

@dispatcher.message(StakeState.selecting_plan)
async def process_stake_plan(message: types.Message, state: FSMContext):
    plan_map = {
        "Starter 2% Forever": (1, None),
        "Pro 3% Forever": (2, None),
        "Elite 4% Forever": (3, None),
        "40-Day 4% Daily": (4, 40),
        "60-Day 3% Daily": (5, 60),
        "100-Day 2.5% Daily": (6, 100)
    }
    if message.text == "Back to Main Menu":
        await message.reply("Returning to main menu.", reply_markup=main_menu)
        await state.clear()  # تغییر از finish به clear
        return
    
    if message.text not in plan_map:
        await message.reply("Invalid plan! Please select a valid staking plan:", reply_markup=stake_plan_menu)
        return
    
    plan_id, duration_days = plan_map[message.text]
    await state.update_data(plan_id=plan_id, duration_days=duration_days)
    data = await state.get_data()
    currency = data["currency"]
    min_stake = await get_min_limit(currency, plan_id, "stake")
    if min_stake == 0:
        await message.reply(f"Enter the amount to stake in {currency}:")
    else:
        if currency == "BNB":
            await message.reply(f"Enter the amount to stake in {currency} (minimum {str(min_stake).rstrip('0').rstrip('.')} {currency}):")
        else:
            await message.reply(f"Enter the amount to stake in {currency} (minimum {min_stake:.2f} {currency}):")
    await StakeState.waiting_for_amount.set()

@dispatcher.message(StakeState.waiting_for_amount)
async def process_stake_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        data = await state.get_data()
        currency = data["currency"]
        plan_id = data["plan_id"]
        duration_days = data["duration_days"]
        user_id = message.from_user.id
        
        min_stake = await get_min_limit(currency, plan_id, "stake")
        if min_stake > 0 and amount < min_stake:
            if currency == "BNB":
                await message.reply(f"Amount must be at least {str(min_stake).rstrip('0').rstrip('.')} {currency} for this plan!")
            else:
                await message.reply(f"Amount must be at least {min_stake:.2f} {currency} for this plan!")
            await state.clear()  # تغییر از finish به clear
            return
        
        user = await get_user(user_id)
        if not user:
            await message.reply("User not found!")
            await state.clear()  # تغییر از finish به clear
            return
        
        balance = {"USDT": user[2], "TRX": user[3], "BNB": user[4], "DOGE": user[5], "TON": user[6]}[currency]
        if amount > balance:
            if currency == "BNB":
                await message.reply(f"Insufficient balance! Your {currency} balance: {balance:.6f} {currency}")
            else:
                await message.reply(f"Insufficient balance! Your {currency} balance: {balance:.2f} {currency}")
            await state.clear()  # تغییر از finish به clear
            return
        
        if await update_balance(user_id, -amount, currency) and await add_stake(user_id, plan_id, amount, duration_days, currency):
            await add_transaction(user_id, "stake", amount, currency)
            if currency == "BNB":
                await message.reply(f"Successfully staked {str(amount).rstrip('0').rstrip('.')} {currency}!")
            else:
                await message.reply(f"Successfully staked {amount:.2f} {currency}!")
        else:
            await message.reply("Failed to stake. Please try again.")
        await state.clear()  # تغییر از finish به clear
        await message.reply("Choose an option:", reply_markup=main_menu)
    except ValueError:
        await message.reply("Please enter a valid number (e.g., 10.5)!")
        await state.clear()  # تغییر از finish به clear

@dispatcher.message(F.text == "💼 Check Balance")
async def check_balance(message: types.Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if user:
        response = "Your Balances:\n"
        response += f"USDT: {user[2]:.2f}\n"
        response += f"TRX: {user[3]:.2f}\n"
        response += f"BNB: {user[4]:.6f}\n"
        response += f"DOGE: {user[5]:.2f}\n"
        response += f"TON: {user[6]:.2f}"
        await message.reply(response)
    else:
        await message.reply("User not found!")

@dispatcher.message(F.text == "📋 Check Staked")
async def check_staked(message: types.Message):
    user_id = message.from_user.id
    stakes = await get_active_stakes(user_id)
    if not stakes:
        await message.reply("You have no active stakes.")
        return
    
    response = "Your Active Stakes:\n"
    plan_names = {1: "Starter 2% Forever", 2: "Pro 3% Forever", 3: "Elite 4% Forever", 
                  4: "40-Day 4% Daily", 5: "60-Day 3% Daily", 6: "100-Day 2.5% Daily"}
    for stake in stakes:
        if len(stake) == 8:
            stake_id, _, plan_id, amount, start_date, duration_days, last_update, is_expired = stake
            currency = "USDT"
        else:
            stake_id, _, plan_id, amount, currency, start_date, duration_days, last_update, is_expired = stake
        start_date = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S.%f')
        days_passed = (datetime.now() - start_date).days
        if duration_days:
            remaining_days = duration_days - days_passed
            if currency == "BNB":
                response += f"{plan_names[plan_id]}: {str(amount).rstrip('0').rstrip('.')} {currency}, {remaining_days} days remaining\n"
            else:
                response += f"{plan_names[plan_id]}: {amount:.2f} {currency}, {remaining_days} days remaining\n"
        else:
            if currency == "BNB":
                response += f"{plan_names[plan_id]}: {str(amount).rstrip('0').rstrip('.')} {currency}, Forever\n"
            else:
                response += f"{plan_names[plan_id]}: {amount:.2f} {currency}, Forever\n"
    await message.reply(response)

@dispatcher.message(F.text == "📈 View Earnings")
async def view_earnings(message: types.Message):
    user_id = message.from_user.id
    earnings_usdt, earnings_trx, earnings_bnb, earnings_doge, earnings_ton = await calculate_total_earnings(user_id)
    response = "Your Earnings:\n"
    response += f"USDT: {earnings_usdt:.2f}\n"
    response += f"TRX: {earnings_trx:.2f}\n"
    response += f"BNB: {earnings_bnb:.6f}\n"
    response += f"DOGE: {earnings_doge:.2f}\n"
    response += f"TON: {earnings_ton:.2f}"
    await message.reply(response, reply_markup=earnings_menu)

@dispatcher.message(F.text == "Transfer to Balance")
async def transfer_earnings_menu(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    earnings_usdt, earnings_trx, earnings_bnb, earnings_doge, earnings_ton = await calculate_total_earnings(user_id)
    response = "Your Earnings:\n"
    response += f"USDT: {earnings_usdt:.2f}\n"
    response += f"TRX: {earnings_trx:.2f}\n"
    response += f"BNB: {earnings_bnb:.6f}\n"
    response += f"DOGE: {earnings_doge:.2f}\n"
    response += f"TON: {earnings_ton:.2f}\n\nEnter the amount and currency to transfer (e.g., '10 USDT'):"
    await message.reply(response)
    await EarningsState.entering_amount.set()

@dispatcher.message(EarningsState.entering_amount)
async def process_earnings_transfer(message: types.Message, state: FSMContext):
    try:
        parts = message.text.split()
        if len(parts) != 2:
            await message.reply("Please enter amount and currency (e.g., '10 USDT')!")
            return
        amount = float(parts[0])
        currency = parts[1].upper()
        user_id = message.from_user.id
        success, result = await transfer_earnings_to_balance(user_id, amount, currency)
        await message.reply(result)
        if success:
            await message.reply("Choose an option:", reply_markup=main_menu)
            await state.clear()  # تغییر از finish به clear
    except ValueError:
        await message.reply("Please enter a valid amount and currency (e.g., '10 USDT')!")
    except Exception as e:
        await message.reply(f"Error: {str(e)}")
        await state.clear()  # تغییر از finish به clear

@dispatcher.message(F.text == "💳 Withdraw")
async def withdraw_menu(message: types.Message):
    await message.reply("Select a currency to withdraw:", reply_markup=withdraw_currency_menu)

@dispatcher.message(F.text.startswith("Withdraw "))
async def process_withdraw_currency(message: types.Message, state: FSMContext):
    currency = message.text.split()[1]
    user_id = message.from_user.id
    wallet_address = await get_wallet_address(user_id, currency)
    await state.update_data(currency=currency)
    if wallet_address:
        await message.reply(f"Your current withdrawal address for {currency} is:\n`{wallet_address}`\n\nIs this correct?", 
                           reply_markup=address_confirmation_menu, parse_mode="Markdown")
        await WithdrawState.confirming_address.set()
    else:
        await message.reply(f"Please enter your {currency} wallet address:")
        await WithdrawState.entering_new_address.set()

@dispatcher.message(WithdrawState.confirming_address)
async def process_address_confirmation(message: types.Message, state: FSMContext):
    if message.text == "Yes":
        data = await state.get_data()
        currency = data["currency"]
        min_withdrawal = await get_min_withdrawal(currency)
        if currency == "BNB":
            await message.reply(f"Enter the amount to withdraw in {currency} (minimum {str(min_withdrawal).rstrip('0').rstrip('.')} {currency}):")
        else:
            await message.reply(f"Enter the amount to withdraw in {currency} (minimum {min_withdrawal:.2f} {currency}):")
        await WithdrawState.entering_amount.set()
    elif message.text == "Change Address":
        data = await state.get_data()
        currency = data["currency"]
        await message.reply(f"Please enter your new {currency} wallet address:")
        await WithdrawState.entering_new_address.set()
    else:
        await message.reply("Returning to main menu.", reply_markup=main_menu)
        await state.finish()

@dispatcher.message(WithdrawState.entering_new_address)
async def process_new_address(message: types.Message, state: FSMContext):
    wallet_address = message.text
    data = await state.get_data()
    currency = data["currency"]
    user_id = message.from_user.id
    await save_wallet_address(user_id, currency, wallet_address)
    min_withdrawal = await get_min_withdrawal(currency)
    if currency == "BNB":
        await message.reply(f"Wallet address updated! Enter the amount to withdraw in {currency} (minimum {str(min_withdrawal).rstrip('0').rstrip('.')} {currency}):")
    else:
        await message.reply(f"Wallet address updated! Enter the amount to withdraw in {currency} (minimum {min_withdrawal:.2f} {currency}):")
    await WithdrawState.entering_amount.set()

@dispatcher.message(WithdrawState.entering_amount)
async def process_withdrawal_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        data = await state.get_data()
        currency = data["currency"]
        user_id = message.from_user.id
        user = await get_user(user_id)
        
        if not user:
            await message.reply("User not found!")
            await state.clear()  # تغییر از finish به clear
            return
        
        balance = {"USDT": user[2], "TRX": user[3], "BNB": user[4], "DOGE": user[5], "TON": user[6]}[currency]
        min_withdrawal = await get_min_withdrawal(currency)
        fee = get_withdrawal_fee(currency)
        total_amount = amount + fee
        
        if amount < min_withdrawal:
            if currency == "BNB":
                await message.reply(f"Amount must be at least {str(min_withdrawal).rstrip('0').rstrip('.')} {currency}!")
            else:
                await message.reply(f"Amount must be at least {min_withdrawal:.2f} {currency}!")
            await state.clear()  # تغییر از finish به clear
            return
        
        if total_amount > balance:
            if currency == "BNB":
                await message.reply(f"Insufficient balance! Your {currency} balance: {balance:.6f} {currency}, Required: {total_amount:.6f} {currency} (including {fee:.6f} fee)")
            else:
                await message.reply(f"Insufficient balance! Your {currency} balance: {balance:.2f} {currency}, Required: {total_amount:.2f} {currency} (including {fee:.2f} fee)")
            await state.clear()  # تغییر از finish به clear
            return
        
        wallet_address = await get_wallet_address(user_id, currency)
        if not wallet_address:
            await message.reply("No wallet address set!")
            await state.clear()  # تغییر از finish به clear
            return
        
        status, last_request_time = await check_last_withdrawal(user_id)
        if status == "Pending" and (datetime.now() - last_request_time).total_seconds() < 24 * 3600:
            await message.reply("You already have a pending withdrawal request within the last 24 hours!")
            await state.clear()  # تغییر از finish به clear
            return
        
        if await update_balance(user_id, -total_amount, currency):
            await add_withdraw_request(user_id, amount, currency, fee, wallet_address)
            await add_transaction(user_id, "withdraw", total_amount, currency)
            if currency == "BNB":
                await message.reply(f"Withdrawal request for {str(amount).rstrip('0').rstrip('.')} {currency} submitted! Fee: {fee:.6f} {currency}. It will be processed soon.")
            else:
                await message.reply(f"Withdrawal request for {amount:.2f} {currency} submitted! Fee: {fee:.2f} {currency}. It will be processed soon.")
        else:
            await message.reply("Failed to process withdrawal. Please try again.")
        await state.clear()  # تغییر از finish به clear
        await message.reply("Choose an option:", reply_markup=main_menu)
    except ValueError:
        await message.reply("Please enter a valid number (e.g., 10.5)!")
        await state.clear()  # تغییر از finish به clear

@dispatcher.message(F.text == "👥 Referral Link")
async def referral_link(message: types.Message):
    user_id = message.from_user.id
    link = f"https://t.me/CoinStakeBot?start={user_id}"
    await message.reply(f"Your referral link:\n{link}\n\nShare this with friends! You’ll earn 5% of their deposits as a bonus.")

@dispatcher.message(F.text == "❓ Guide")
async def send_guide(message: types.Message):
    guide = (
        "📖 *CoinStake Guide*\n\n"
        "1. *Deposit*: Add funds using supported currencies.\n"
        "2. *Stake*: Choose a plan and stake your funds to earn daily profits.\n"
        "3. *Withdraw*: Request withdrawals anytime (processed within 24h).\n"
        "4. *Earnings*: View and transfer your profits to your balance.\n"
        "5. *Referral*: Invite friends and earn 5% of their deposits.\n\n"
        "Supported currencies: USDT, TRX, BNB, DOGE, TON.\n"
        "For help, contact support!"
    )
    await message.reply(guide, parse_mode="Markdown")

@dispatcher.message(F.text == "Back to Main Menu")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    await state.clear()  # تغییر از finish به clear
    await message.reply("Returning to main menu.", reply_markup=main_menu)

@dispatcher.callback_query(F.callback_data == "view_users")
async def view_users(callback: types.CallbackQuery):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username FROM users")
        users = cursor.fetchall()
        conn.close()
        response = "Users:\n" + "\n".join([f"ID: {user[0]}, Username: @{user[1]}" for user in users])
        await callback.message.edit_text(response if users else "No users found.")
    await callback.answer()

@dispatcher.callback_query(F.callback_data == "edit_balance")
async def edit_balance(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Enter user ID and new balance (e.g., '12345 100 USDT'):")
    await AdminState.waiting_for_edit_balance.set()
    await callback.answer()

@dispatcher.message(AdminState.waiting_for_edit_balance)
async def process_edit_balance(message: types.Message, state: FSMContext):
    try:
        parts = message.text.split()
        if len(parts) != 3:
            await message.reply("Format: 'user_id amount currency' (e.g., '12345 100 USDT')")
            return
        user_id, amount, currency = int(parts[0]), float(parts[1]), parts[2].upper()
        conn = await db_connect()
        cursor = conn.cursor()
        cursor.execute(f"UPDATE users SET balance_{currency.lower()} = ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        conn.close()
        await message.reply(f"Balance for user {user_id} updated to {amount} {currency}!")
        await state.clear()  # تغییر از finish به clear
        admin_menu = await get_admin_menu(message.from_user.username or "Unknown")
        await message.reply("Admin Panel:", reply_markup=admin_menu)
    except (ValueError, sqlite3.Error) as e:
        await message.reply(f"Error: {str(e)}. Please use format 'user_id amount currency'.")
        await state.clear()  # تغییر از finish به clear

@dispatcher.callback_query(F.callback_data == "delete_user")
async def delete_user(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Enter the user ID to delete:")
    await AdminState.waiting_for_delete_user.set()
    await callback.answer()

@dispatcher.message(AdminState.waiting_for_delete_user)
async def process_delete_user(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        conn = await db_connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await message.reply(f"User {user_id} deleted!")
        await state.clear()  # تغییر از finish به clear
        admin_menu = await get_admin_menu(message.from_user.username or "Unknown")
        await message.reply("Admin Panel:", reply_markup=admin_menu)
    except ValueError:
        await message.reply("Please enter a valid user ID!")
        await state.clear()  # تغییر از finish به clear

@dispatcher.callback_query(F.callback_data == "stats")
async def bot_stats(callback: types.CallbackQuery):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT SUM(amount) FROM stakes WHERE is_expired = 0")
        total_staked = cursor.fetchone()[0] or 0
        cursor.execute("SELECT SUM(amount) FROM transactions WHERE transaction_type = 'deposit'")
        total_deposits = cursor.fetchone()[0] or 0
        conn.close()
        response = f"Bot Stats:\nUsers: {total_users}\nTotal Staked: {total_staked:.2f}\nTotal Deposits: {total_deposits:.2f}"
        await callback.message.edit_text(response)
    await callback.answer()

@dispatcher.callback_query(F.callback_data == "edit_stake_limits")
async def edit_stake_limits(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Enter currency, plan ID, and min amount (e.g., 'USDT 1 50'):")
    await AdminState.waiting_for_edit_stake_limit.set()
    await callback.answer()

@dispatcher.message(AdminState.waiting_for_edit_stake_limit)
async def process_edit_stake_limit(message: types.Message, state: FSMContext):
    try:
        parts = message.text.split()
        if len(parts) != 3:
            await message.reply("Format: 'currency plan_id min_amount' (e.g., 'USDT 1 50')")
            return
        currency, plan_id, min_amount = parts[0].upper(), int(parts[1]), float(parts[2])
        if await update_min_limit(currency, plan_id, min_amount, "stake"):
            await message.reply(f"Stake limit for {currency} plan {plan_id} updated to {min_amount}!")
        else:
            await message.reply("Failed to update stake limit.")
        await state.clear()  # تغییر از finish به clear
        admin_menu = await get_admin_menu(message.from_user.username or "Unknown")
        await message.reply("Admin Panel:", reply_markup=admin_menu)
    except ValueError:
        await message.reply("Invalid format! Use 'currency plan_id min_amount' (e.g., 'USDT 1 50')")
        await state.clear()  # تغییر از finish به clear

@dispatcher.callback_query(F.callback_data == "edit_deposit_limits")
async def edit_deposit_limits(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Enter currency and min deposit amount (e.g., 'USDT 20'):")
    await AdminState.waiting_for_edit_deposit_limit.set()
    await callback.answer()

@dispatcher.message(AdminState.waiting_for_edit_deposit_limit)
async def process_edit_deposit_limit(message: types.Message, state: FSMContext):
    try:
        parts = message.text.split()
        if len(parts) != 2:
            await message.reply("Format: 'currency min_amount' (e.g., 'USDT 20')")
            return
        currency, min_amount = parts[0].upper(), float(parts[1])
        if await update_min_limit(currency, 0, min_amount, "deposit"):
            await message.reply(f"Deposit limit for {currency} updated to {min_amount}!")
        else:
            await message.reply("Failed to update deposit limit.")
        await state.clear()  # تغییر از finish به clear
        admin_menu = await get_admin_menu(message.from_user.username or "Unknown")
        await message.reply("Admin Panel:", reply_markup=admin_menu)
    except ValueError:
        await message.reply("Invalid format! Use 'currency min_amount' (e.g., 'USDT 20')")
        await state.clear()  # تغییر از finish به clear

@dispatcher.callback_query(F.callback_data == "add_admin")
async def add_admin(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Enter the user ID to add as admin:")
    await AdminState.waiting_for_add_admin_id.set()
    await callback.answer()

@dispatcher.message(AdminState.waiting_for_add_admin_id)
async def process_add_admin(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        conn = await db_connect()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
        conn.commit()
        conn.close()
        await message.reply(f"User {user_id} added as admin!")
        await state.clear()  # تغییر از finish به clear
        admin_menu = await get_admin_menu(message.from_user.username or "Unknown")
        await message.reply("Admin Panel:", reply_markup=admin_menu)
    except ValueError:
        await message.reply("Please enter a valid user ID!")
        await state.clear()  # تغییر از finish به clear

@dispatcher.callback_query(F.callback_data == "remove_admin")
async def remove_admin(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Enter the user ID to remove from admins:")
    await AdminState.waiting_for_remove_admin_id.set()
    await callback.answer()

@dispatcher.message(AdminState.waiting_for_remove_admin_id)
async def process_remove_admin(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        conn = await db_connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await message.reply(f"User {user_id} removed from admins!")
        await state.clear()  # تغییر از finish به clear
        admin_menu = await get_admin_menu(message.from_user.username or "Unknown")
        await message.reply("Admin Panel:", reply_markup=admin_menu)
    except ValueError:
        await message.reply("Please enter a valid user ID!")
        await state.clear()  # تغییر از finish به clear

@dispatcher.callback_query(F.callback_data.startswith("complete_"))
async def complete_withdrawal_request(callback: types.CallbackQuery):
    request_id = int(callback.data.split("_")[1])
    await complete_withdrawal(request_id)
    details = await get_withdrawal_details(request_id)
    if details:
        user_id, amount, currency, wallet_address = details
        if currency == "BNB":
            await bot.send_message(user_id, f"Your withdrawal of {str(amount).rstrip('0').rstrip('.')} {currency} to {wallet_address} has been completed!")
        else:
            await bot.send_message(user_id, f"Your withdrawal of {amount:.2f} {currency} to {wallet_address} has been completed!")
    await callback.message.edit_text(f"Withdrawal ID {request_id} marked as completed.")
    await callback.answer()

@dispatcher.callback_query(F.callback_data.startswith("reject_"))
async def reject_withdrawal_request(callback: types.CallbackQuery):
    request_id = int(callback.data.split("_")[1])
    details = await get_withdrawal_details(request_id)
    if details:
        user_id, amount, currency, wallet_address = details
        fee = get_withdrawal_fee(currency)
        total_amount = amount + fee
        await update_balance(user_id, total_amount, currency)
        await reject_withdrawal(request_id)
        if currency == "BNB":
            await bot.send_message(user_id, f"Your withdrawal of {str(amount).rstrip('0').rstrip('.')} {currency} to {wallet_address} was rejected. {str(total_amount).rstrip('0').rstrip('.')} {currency} has been refunded.")
        else:
            await bot.send_message(user_id, f"Your withdrawal of {amount:.2f} {currency} to {wallet_address} was rejected. {total_amount:.2f} {currency} has been refunded.")
    await callback.message.edit_text(f"Withdrawal ID {request_id} rejected and refunded.")
    await callback.answer()

async def main():
    await initialize_database()
    asyncio.create_task(schedule_reports())
    await bot.delete_webhook(drop_pending_updates=True)
    webhook_url = "https://new-staking-bot.onrender.com/telegram-webhook"
    await bot.set_webhook(webhook_url)
    logging.info(f"Webhook set to {webhook_url}")
    app_runner = web.AppRunner(app)
    await app_runner.setup()
    site = web.TCPSite(app_runner, '0.0.0.0', 8080)
    await site.start()
    logging.info("Server started on port 8080")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())