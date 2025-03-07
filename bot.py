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

API_TOKEN = os.getenv("API_TOKEN", "8149978835:AAFcLTmqXz8o0VYu0zXiLQXElcsMI03J8CA")
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY", "4ECPB3V-PH6MKES-GZR79RZ-8HMMRSC")
IPN_SECRET = os.getenv("IPN_SECRET", "1N6xRI+EGoFRW+txIHd5O5srB9uq64ZT")
ADMIN_ID = None
logging.basicConfig(level=logging.INFO)
logging.info(f"Bot initialized with token: {API_TOKEN}")

app = web.Application()
bot = Bot(token=API_TOKEN)
dispatcher = Dispatcher()

db_lock = asyncio.Lock()

async def db_connect():
    async with db_lock:
        for attempt in range(3):
            try:
                # تغییر مسیر به دیسک Render
                conn = sqlite3.connect("/opt/render/project/db/staking_bot.db", timeout=30)
                return conn
            except sqlite3.OperationalError as e:
                logging.error(f"Database connection attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(1)
                else:
                    raise

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
        if cursor.fetchone()[0] == 0:  # فقط اگه هیچ حداقل دیپازیتی نباشه
            initial_deposit_limits = [
                ("USDT", 0, 20.0), ("TRX", 0, 40.0), ("BNB", 0, 0.02), ("DOGE", 0, 150.0), ("TON", 0, 8.0)
            ]
            cursor.executemany("INSERT INTO limits (currency, plan_id, min_amount, type) VALUES (?, ?, ?, 'deposit')", initial_deposit_limits)
            logging.info("Initialized default deposit limits.")
        
        cursor.execute("SELECT COUNT(*) FROM limits WHERE type = 'stake'")
        if cursor.fetchone()[0] == 0:  # فقط اگه هیچ حداقل استیکی نباشه
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
            # تغییر فرمت لاگ به اعشار
            logging.info(f"Crediting referral bonus: {bonus_amount:.5f} {currency} to referrer {referrer_id}")
            success = await update_balance(referrer_id, bonus_amount, currency)
            if success:
                await add_transaction(referrer_id, "referral_bonus", bonus_amount, currency)
                if currency == "BNB":
                    # تغییر فرمت پیام به اعشار
                    await bot.send_message(referrer_id, f"Your balance has been increased by {bonus_amount:.5f} {currency} as a referral bonus from user {user_id}.")
                else:
                    await bot.send_message(referrer_id, f"Your balance has been increased by {bonus_amount:.2f} {currency} as a referral bonus from user {user_id}.")
            else:
                # تغییر فرمت لاگ به اعشار
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
    
    admin_menu = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="View Users", callback_data="view_users"),
         InlineKeyboardButton(text="Edit Balance", callback_data="edit_balance")],
        [InlineKeyboardButton(text="Delete User", callback_data="delete_user"),
         InlineKeyboardButton(text="Bot Stats", callback_data="stats")],
        [InlineKeyboardButton(text="Edit Stake Limits", callback_data="edit_stake_limits"),
         InlineKeyboardButton(text="Edit Deposit Limits", callback_data="edit_deposit_limits")],
        [InlineKeyboardButton(text="Edit Earnings", callback_data="admin_edit_earnings")]  # دکمه جدید
    ])
      
    if username.lower() in ["coinstakebot_admin", "tyhi87655"]:
        admin_menu.inline_keyboard.append([
            InlineKeyboardButton(text="Add Admin", callback_data="add_admin"),
            InlineKeyboardButton(text="Remove Admin", callback_data="remove_admin")
        ])
    
    await message.reply("Admin Panel:", reply_markup=admin_menu)

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
        if time_diff.total_seconds() < 24 * 3600:
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
    username = message.from_user.username or "Unknown"
    user = await get_user(user_id)
    if not user:
        await add_user(user_id, username)
        user = await get_user(user_id)
    
    balance_usdt, balance_trx, balance_bnb, balance_doge, balance_ton = user[2], user[3], user[4], user[5], user[6]
    # تغییر فرمت به اعشاری برای BNB
    await message.reply(f"Your balance:\n{balance_usdt:.2f} USDT\n{balance_trx:.2f} TRX\n{balance_bnb:.5f} BNB\n{balance_doge:.2f} DOGE\n{balance_ton:.2f} TON")

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
            1: "Starter 2% Forever: Unlimited",
            2: "Pro 3% Forever: Unlimited",
            3: "Elite 4% Forever: Unlimited",
            4: "40-Day 4% Daily: 4% (40 days)",
            5: "60-Day 3% Daily: 3% (60 days)",
            6: "100-Day 2.5% Daily: 2.5% (100 days)"
        }[plan_id]
        
        if currency == "BNB":
            response += f"- {plan_desc}: {amount:,.6f} {currency} (Started: {start_date})\n"
        else:
            response += f"- {plan_desc}: {amount:,.2f} {currency} (Started: {start_date})\n"
    await message.reply(response)

@dispatcher.message(Command("viewearnings"))
async def view_earnings_command(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if user:
        earnings_usdt, earnings_trx, earnings_bnb, earnings_doge, earnings_ton = await calculate_total_earnings(user_id)
        await message.reply(f"Your total earnings:\n{str(earnings_usdt).rstrip('0').rstrip('.')} USDT\n{str(earnings_trx).rstrip('0').rstrip('.')} TRX\n{str(earnings_bnb).rstrip('0').rstrip('.')} BNB\n{str(earnings_doge).rstrip('0').rstrip('.')} DOGE\n{str(earnings_ton).rstrip('0').rstrip('.')} TON", reply_markup=earnings_menu)
        await state.set_state(EarningsState.choosing_action)
    else:
        await message.reply("User not found.")

@dispatcher.message(Command("referral"))
async def referral_command(message: types.Message):
    user_id = message.from_user.id
    bot_info = await bot.get_me()
    referral_link = f"https://t.me/CoinStakeBot?start={user_id}"
    
    encoded_link = urllib.parse.quote(referral_link)
    share_button = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➦ Share", url=f"https://t.me/share/url?url={encoded_link}&text=Join CoinStake staking bot!")]
    ])
    
    await message.reply(f"Your referral link: {referral_link}", reply_markup=share_button)

@dispatcher.message(Command("pending"))
async def pending_withdrawals_command(message: types.Message):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        await message.reply("You are not an admin!")
        return
    
    requests = await get_pending_withdrawals()
    if not requests:
        await message.reply("No pending withdrawals in the last 12 hours.")
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
    
    await message.reply(report, reply_markup=keyboard)

@dispatcher.message(Command("userstats"))
async def user_stats_command(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        await message.reply("You are not an admin!")
        return
    
    try:
        target_user_id = int(message.text.split()[1])
    except (IndexError, ValueError):
        await message.reply("Please provide a valid user ID (e.g., /userstats 123456)")
        return
    
    user = await get_user(target_user_id)
    if not user:
        await message.reply(f"No user found with ID {target_user_id}.")
        return
    
    balance_usdt, balance_trx, balance_bnb, balance_doge, balance_ton = user[2], user[3], user[4], user[5], user[6]
    earnings_usdt, earnings_trx, earnings_bnb, earnings_doge, earnings_ton = await calculate_total_earnings(target_user_id)
    
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT amount, currency, timestamp FROM transactions WHERE user_id = ? AND transaction_type = 'deposit'", (target_user_id,))
        deposits = cursor.fetchall()
        conn.close()
    
    report = f"Stats for User ID {target_user_id} (@{user[1]}):\n\n"
    report += "Balances:\n"
    report += f"- USDT: {str(balance_usdt).rstrip('0').rstrip('.')}\n"
    report += f"- TRX: {str(balance_trx).rstrip('0').rstrip('.')}\n"
    report += f"- BNB: {str(balance_bnb).rstrip('0').rstrip('.')}\n"
    report += f"- DOGE: {str(balance_doge).rstrip('0').rstrip('.')}\n"
    report += f"- TON: {str(balance_ton).rstrip('0').rstrip('.')}\n\n"
    
    report += "Earnings:\n"
    report += f"- USDT: {str(earnings_usdt).rstrip('0').rstrip('.')}\n"
    report += f"- TRX: {str(earnings_trx).rstrip('0').rstrip('.')}\n"
    report += f"- BNB: {str(earnings_bnb).rstrip('0').rstrip('.')}\n"
    report += f"- DOGE: {str(earnings_doge).rstrip('0').rstrip('.')}\n"
    report += f"- TON: {str(earnings_ton).rstrip('0').rstrip('.')}\n\n"
    
    report += "Deposits:\n"
    if deposits:
        for deposit in deposits:
            amount, currency, timestamp = deposit
            report += f"- {str(amount).rstrip('0').rstrip('.')} {currency} on {timestamp}\n"
    else:
        report += "- No deposits found.\n"
    
    await message.reply(report)

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

@dispatcher.message(F.text == "❓ Guide")
async def guide_command(message: types.Message):
    guide_text = (
        "CoinStake Guide:\n"
        "- 💰 Deposit: Add funds to your wallet.\n"
        "- 💳 Withdraw: Request a withdrawal.\n"
        "- 💸 Stake: Lock funds for daily earnings.\n"
        "- 💼 Check Balance: See your funds.\n"
        "- 📋 Check Staked: View active stakes.\n"
        "- 📈 View Earnings: Check your profits.\n"
        "- 👥 Referral Link: Invite friends, earn 5% bonus.\n"
        "Need assistance? Contact our admin: @CoinStakeBot_Admin"
    )
    await message.reply(guide_text, reply_markup=main_menu)

@dispatcher.message(Command("checkuser"))
async def check_user_command(message: types.Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if user:
        logging.info(f"User {user_id} data: {user}")
        await message.reply(f"User data: {user}")
    else:
        await message.reply("User not found.")

@dispatcher.message(DepositState.selecting_currency)
async def process_deposit_currency(message: types.Message, state: FSMContext):
    currency_map = {
        "Deposit USDT": "USDT",
        "Deposit TRX": "TRX",
        "Deposit BNB": "BNB",
        "Deposit DOGE": "DOGE",
        "Deposit TON": "TON"
    }
    if message.text == "Back to Main Menu":
        await message.reply("Returning to main menu.", reply_markup=main_menu)
        await state.clear()
        return
    elif message.text in currency_map:
        currency = currency_map[message.text]
        await state.update_data(currency=currency)
        await message.reply(f"Please enter the amount of {currency} to deposit:", reply_markup=main_menu)
        await state.set_state(DepositState.waiting_for_amount)
    else:
        await message.reply("Please select a valid currency.", reply_markup=deposit_currency_menu)

@dispatcher.message(DepositState.waiting_for_amount)
async def process_deposit_amount(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    currency = data["currency"]
    
    if message.text == "Back to Main Menu":
        await message.reply("Returning to main menu.", reply_markup=main_menu)
        await state.clear()
        return
    
    try:
        amount = float(message.text)
        if amount <= 0:
            await message.reply("Please enter a positive amount.", reply_markup=main_menu)
            return
        
        min_deposit = await get_min_deposit(currency)
        if amount < min_deposit:
            if currency == "BNB":
                await message.reply(f"Minimum deposit for {currency} is {str(min_deposit).rstrip('0').rstrip('.')} {currency}. Please enter a higher amount.", reply_markup=main_menu)
            else:
                await message.reply(f"Minimum deposit for {currency} is {min_deposit:.2f} {currency}. Please enter a higher amount.", reply_markup=main_menu)
            return
        
        address = await generate_payment_address(user_id, amount, currency)
        if address:
            await save_deposit_address(user_id, currency, address)
            network = "TRC-20" if currency in ["USDT", "TRX"] else "BEP-20" if currency == "BNB" else "Main Network"
            if currency == "BNB":
                await message.reply(f"Please send {str(amount).rstrip('0').rstrip('.')} {currency} to this {network} address within 20 minutes (sent in the next message). Your account will be credited automatically after confirmation.", reply_markup=main_menu)
            else:
                await message.reply(f"Please send {amount:.2f} {currency} to this {network} address within 20 minutes (sent in the next message). Your account will be credited automatically after confirmation.", reply_markup=main_menu)
            await message.reply(address)
        else:
            await message.reply("Failed to generate deposit address. Check if API key is correct or try again later.", reply_markup=main_menu)
        await state.clear()
    except ValueError:
        await message.reply("Invalid amount. Please enter a number.", reply_markup=main_menu)

@dispatcher.message(StakeState.selecting_currency)
async def process_stake_currency(message: types.Message, state: FSMContext):
    if message.text == "Back to Main Menu":
        await message.reply("Returning to main menu.", reply_markup=main_menu)
        await state.clear()
        return
    
    currency_map = {
        "Stake USDT": "USDT",
        "Stake TRX": "TRX",
        "Stake BNB": "BNB",
        "Stake DOGE": "DOGE",
        "Stake TON": "TON"
    }
    if message.text not in currency_map:
        await message.reply("Please select a valid currency.", reply_markup=stake_currency_menu)
        return
    
    currency = currency_map[message.text]
    await state.update_data(currency=currency)
    await message.reply(f"Choose a staking plan for {currency}:", reply_markup=stake_plan_menu)
    await state.set_state(StakeState.selecting_plan)

@dispatcher.message(StakeState.selecting_plan, F.text.in_({"Starter 2% Forever", "Pro 3% Forever", "Elite 4% Forever", "40-Day 4% Daily", "60-Day 3% Daily", "100-Day 2.5% Daily", "Back to Main Menu"}))
async def process_plan_selection(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    currency = data["currency"]
    
    plan_descriptions = {
        "Starter 2% Forever": f"Starter 2% Forever: 2% Daily profit, unlimited duration (From {await get_min_limit(currency, 1, 'stake')} {currency})",
        "Pro 3% Forever": f"Pro 3% Forever: 3% Daily profit, unlimited duration (From {await get_min_limit(currency, 2, 'stake')} {currency})",
        "Elite 4% Forever": f"Elite 4% Forever: 4% Daily profit, unlimited duration (From {await get_min_limit(currency, 3, 'stake')} {currency})",
        "40-Day 4% Daily": f"40-Day 4% Daily: 4% Daily profit for 40 days (From {await get_min_limit(currency, 4, 'stake')} {currency})",
        "60-Day 3% Daily": f"60-Day 3% Daily: 3% Daily profit for 60 days (From {await get_min_limit(currency, 5, 'stake')} {currency})",
        "100-Day 2.5% Daily": f"100-Day 2.5% Daily: 2.5% Daily profit for 100 days (From {await get_min_limit(currency, 6, 'stake')} {currency})"
    }
    
    if message.text == "Back to Main Menu":
        await message.reply("Returning to main menu.", reply_markup=main_menu)
        await state.clear()
        return
    
    selected_plan = message.text
    if selected_plan in plan_descriptions:
        await message.reply(plan_descriptions[selected_plan])
        await message.reply(f"Please enter the amount of {currency} to stake:", reply_markup=stake_plan_menu)
        plan_id = {
            "Starter 2% Forever": 1,
            "Pro 3% Forever": 2,
            "Elite 4% Forever": 3,
            "40-Day 4% Daily": 4,
            "60-Day 3% Daily": 5,
            "100-Day 2.5% Daily": 6
        }[selected_plan]
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
        1: "Starter 2% Forever",
        2: "Pro 3% Forever",
        3: "Elite 4% Forever",
        4: "40-Day 4% Daily",
        5: "60-Day 3% Daily",
        6: "100-Day 2.5% Daily"
    }
    
    stake_menu_options = ["Starter 2% Forever", "Pro 3% Forever", "Elite 4% Forever", "40-Day 4% Daily", "60-Day 3% Daily", "100-Day 2.5% Daily", "Back to Main Menu"]
    if message.text in stake_menu_options:
        await process_plan_selection(message, state)
        return
    
    try:
        amount = float(message.text)
        if amount <= 0:
            await message.reply("Please enter a positive amount.", reply_markup=stake_plan_menu)
            return
        
        min_stake = await get_min_limit(currency, plan_id, "stake")
        if amount < min_stake:
            if currency == "BNB":
                await message.reply(f"Amount must be at least {min_stake:.6f} {currency} for {plan_names[plan_id]}.", reply_markup=stake_plan_menu)
            else:
                await message.reply(f"Amount must be at least {min_stake:.2f} {currency} for {plan_names[plan_id]}.", reply_markup=stake_plan_menu)
            return
        
        user = await get_user(user_id)
        balance = user[2] if currency == "USDT" else user[3] if currency == "TRX" else user[4] if currency == "BNB" else user[5] if currency == "DOGE" else user[6]
        if balance < amount:
            await message.reply(f"Insufficient {currency} balance.", reply_markup=stake_plan_menu)
            return
        
        duration_days = {1: None, 2: None, 3: None, 4: 40, 5: 60, 6: 100}[plan_id]
        await update_balance(user_id, -amount, currency)
        await add_stake(user_id, plan_id, amount, duration_days, currency)
        await add_transaction(user_id, f"stake_plan_{plan_id}", amount, currency)
        if currency == "BNB":
            await message.reply(f"Staked {amount:,.6f} {currency} in {plan_names[plan_id]}. Check your stakes with 'Check Staked'.", reply_markup=main_menu)
        else:
            await message.reply(f"Staked {amount:,.2f} {currency} in {plan_names[plan_id]}. Check your stakes with 'Check Staked'.", reply_markup=main_menu)
        await state.clear()
    except ValueError:
        await message.reply("Invalid amount. Please enter a number.", reply_markup=stake_plan_menu)

@dispatcher.message(EarningsState.choosing_action)
async def process_earnings_action(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text == "Transfer to Balance":
        user = await get_user(user_id)
        earnings_usdt, earnings_trx, earnings_bnb, earnings_doge, earnings_ton = user[5], user[6], user[7], user[8], user[9]
        await message.reply(f"Please enter the amount you want to transfer to your balance:\nAvailable:\n{earnings_usdt:,.2f} USDT\n{earnings_trx:,.2f} TRX\n{earnings_bnb:,.6f} BNB\n{earnings_doge:,.2f} DOGE\n{earnings_ton:,.2f} TON\nSpecify currency (e.g., '10 TRX' or '5 USDT'):", reply_markup=earnings_menu)
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
        if len(parts) != 2 or parts[1] not in ["USDT", "TRX", "BNB", "DOGE", "TON"]:
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
    
    currency_map = {
        "Withdraw USDT": "USDT",
        "Withdraw TRX": "TRX",
        "Withdraw BNB": "BNB",
        "Withdraw DOGE": "DOGE",
        "Withdraw TON": "TON"
    }
    if message.text not in currency_map:
        await message.reply("Please select a valid currency.", reply_markup=withdraw_currency_menu)
        return
    
    currency = currency_map[message.text]
    await state.update_data(currency=currency)
    
    user = await get_user(user_id)
    earnings = user[5] if currency == "USDT" else user[6] if currency == "TRX" else user[7] if currency == "BNB" else user[8] if currency == "DOGE" else user[9]
    if currency == "BNB":
        await message.reply(f"Your available earnings for {currency}: {earnings:,.6f} {currency}. Enter the amount to withdraw:", reply_markup=main_menu)
    else:
        await message.reply(f"Your available earnings for {currency}: {earnings:,.2f} {currency}. Enter the amount to withdraw:", reply_markup=main_menu)
    await state.set_state(WithdrawState.entering_amount)

@dispatcher.message(WithdrawState.entering_amount)
async def process_withdraw_amount(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    currency = data["currency"]
    
    min_withdrawal = await get_min_withdrawal(currency)
    fee = get_withdrawal_fee(currency)
    
    try:
        amount = float(message.text)
        if amount < min_withdrawal:
            if currency == "BNB":
                await message.reply(f"Amount must be at least {min_withdrawal:.6f} {currency}.", reply_markup=main_menu)
            else:
                await message.reply(f"Amount must be at least {min_withdrawal:.2f} {currency}.", reply_markup=main_menu)
            return
        
        total_amount = amount + fee
        user = await get_user(user_id)
        earnings = user[5] if currency == "USDT" else user[6] if currency == "TRX" else user[7] if currency == "BNB" else user[8] if currency == "DOGE" else user[9]
        
        if earnings < total_amount:
            await message.reply(f"Insufficient {currency} earnings.", reply_markup=main_menu)
            return
        
        wallet_address = await get_wallet_address(user_id, currency)
        if not wallet_address:
            network = "TRC-20" if currency in ["USDT", "TRX"] else "BEP-20" if currency == "BNB" else "Main Network"
            if currency == "BNB":
                await message.reply(f"The network fee for withdrawing {currency} is {fee:.6f} {currency}. Please enter your {network} {currency} wallet address:", reply_markup=main_menu)
            else:
                await message.reply(f"The network fee for withdrawing {currency} is {fee:.4f} {currency}. Please enter your {network} {currency} wallet address:", reply_markup=main_menu)
            await state.set_state(WithdrawState.entering_new_address)
            return
        
        if await update_earnings(user_id, -total_amount, currency):
            await add_withdraw_request(user_id, amount, currency, fee, wallet_address)
            network = "TRC-20" if currency in ["USDT", "TRX"] else "BEP-20" if currency == "BNB" else "Main Network"
            if currency == "BNB":
                await message.reply(f"The network fee for withdrawing {currency} is {fee:.6f} {currency}. {amount:,.6f} {currency} has been deducted from your earnings (including fee) and will be transferred to your {network} wallet ({wallet_address}) within 24 hours after review.", reply_markup=main_menu)
            else:
                await message.reply(f"The network fee for withdrawing {currency} is {fee:.4f} {currency}. {amount:,.2f} {currency} has been deducted from your earnings (including fee) and will be transferred to your {network} wallet ({wallet_address}) within 24 hours after review.", reply_markup=main_menu)
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
    min_withdrawal = await get_min_withdrawal(currency)
    if currency == "BNB":
        await message.reply(f"Network fee for withdrawing {currency} is {fee:.6f} {currency}. Enter the amount to withdraw (minimum {min_withdrawal:.6f} {currency}):", reply_markup=main_menu)
    else:
        await message.reply(f"Network fee for withdrawing {currency} is {fee:.4f} {currency}. Enter the amount to withdraw (minimum {min_withdrawal:.2f} {currency}):", reply_markup=main_menu)
    await state.set_state(WithdrawState.entering_amount)

@dispatcher.callback_query(F.data == "view_users")
async def process_view_users(callback: types.CallbackQuery):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username FROM users")
        users = cursor.fetchall()
        conn.close()
        if not users:
            await callback.message.reply("No users found!")
        else:
            response = "Users:\n" + "\n".join(f"ID: {user[0]}, Username: @{user[1]}" for user in users)
            await callback.message.reply(response)
    await callback.answer()

@dispatcher.callback_query(F.data == "edit_balance")
async def process_edit_balance(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminState.waiting_for_edit_balance)
    await callback.message.reply("Please enter the user ID and new balance (e.g., '123456 50 TRX' or '123456 0.1 BNB'):")
    current_state = await state.get_state()
    logging.info(f"State set to: {current_state}")
    await callback.answer()

@dispatcher.message(AdminState.waiting_for_edit_balance)
async def edit_balance(message: types.Message, state: FSMContext):
    logging.info(f"Received message in edit_balance: {message.text}")
    try:
        parts = message.text.split()
        if len(parts) != 3 or parts[2] not in ["USDT", "TRX", "BNB", "DOGE", "TON"]:
            await message.reply("Invalid input. Use format: 'user_id amount currency' (e.g., '123456 50 TRX')")
            return
        user_id = int(parts[0])
        amount = float(parts[1])
        currency = parts[2]
        
        conn = await db_connect()
        if conn:
            cursor = conn.cursor()
            if currency == "USDT":
                cursor.execute("UPDATE users SET balance_usdt = ? WHERE user_id = ?", (amount, user_id))
            elif currency == "TRX":
                cursor.execute("UPDATE users SET balance_trx = ? WHERE user_id = ?", (amount, user_id))
            elif currency == "BNB":
                cursor.execute("UPDATE users SET balance_bnb = ? WHERE user_id = ?", (amount, user_id))
            elif currency == "DOGE":
                cursor.execute("UPDATE users SET balance_doge = ? WHERE user_id = ?", (amount, user_id))
            elif currency == "TON":
                cursor.execute("UPDATE users SET balance_ton = ? WHERE user_id = ?", (amount, user_id))
            conn.commit()
            conn.close()
            await message.reply(f"Balance updated for user {user_id} to {amount} {currency}")
        await state.clear()
    except ValueError:
        await message.reply("Invalid input. Please enter a valid number for ID and amount.")
    except Exception as e:
        await message.reply(f"Error: {e}")

@dispatcher.callback_query(F.data == "edit_stake_limits")
async def process_edit_stake_limits(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminState.waiting_for_edit_stake_limit)
    await callback.message.reply("Please enter the currency, plan ID, and new minimum stake (e.g., 'USDT 1 50' for Starter 2% Forever):")
    await callback.answer()

@dispatcher.message(AdminState.waiting_for_edit_stake_limit)
async def edit_stake_limit(message: types.Message, state: FSMContext):
    try:
        parts = message.text.split()
        if len(parts) != 3 or parts[0] not in ["USDT", "TRX", "BNB", "DOGE", "TON"] or int(parts[1]) not in [1, 2, 3, 4, 5, 6]:
            await message.reply("Invalid input. Use format: 'currency plan_id amount' (e.g., 'USDT 1 50')")
            return
        currency = parts[0]
        plan_id = int(parts[1])
        min_amount = float(parts[2])
        
        if await update_min_limit(currency, plan_id, min_amount, "stake"):
            await message.reply(f"Minimum stake for {currency} plan {plan_id} updated to {min_amount} {currency}")
        else:
            await message.reply("Failed to update stake limit.")
        await state.clear()
    except ValueError:
        await message.reply("Invalid input. Please enter valid numbers for plan ID and amount.")
    except Exception as e:
        await message.reply(f"Error: {e}")

@dispatcher.callback_query(F.data == "edit_deposit_limits")
async def process_edit_deposit_limits(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminState.waiting_for_edit_deposit_limit)
    await callback.message.reply("Please enter the currency and new minimum deposit (e.g., 'USDT 20'):")
    await callback.answer()

@dispatcher.message(AdminState.waiting_for_edit_deposit_limit)
async def edit_deposit_limit(message: types.Message, state: FSMContext):
    try:
        parts = message.text.split()
        if len(parts) != 2 or parts[0] not in ["USDT", "TRX", "BNB", "DOGE", "TON"]:
            await message.reply("Invalid input. Use format: 'currency amount' (e.g., 'USDT 20')")
            return
        currency = parts[0]
        min_amount = float(parts[1])
        
        if await update_min_limit(currency, 0, min_amount, "deposit"):
            await message.reply(f"Minimum deposit for {currency} updated to {min_amount} {currency}")
        else:
            await message.reply("Failed to update deposit limit.")
        await state.clear()
    except ValueError:
        await message.reply("Invalid input. Please enter a valid number for amount.")
    except Exception as e:
        await message.reply(f"Error: {e}")

@dispatcher.callback_query(F.data == "delete_user")
async def process_delete_user(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.reply("Please enter the user ID to delete:")
    await state.set_state(AdminState.waiting_for_delete_user)
    await callback.answer()

@dispatcher.message(AdminState.waiting_for_delete_user)
async def delete_user(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        conn = await db_connect()
        if conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM stakes WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM transactions WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM wallets WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            await message.reply(f"User with ID {user_id} has been deleted!")
        await state.clear()
    except ValueError:
        await message.reply("Invalid ID. Please enter a number.")

@dispatcher.callback_query(F.data == "stats")
async def process_stats(callback: types.CallbackQuery):
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(user_id), SUM(balance_usdt), SUM(balance_trx), SUM(balance_bnb), SUM(balance_doge), SUM(balance_ton) FROM users")
        stats = cursor.fetchone()
        conn.close()
        user_count, total_usdt, total_trx, total_bnb, total_doge, total_ton = stats
        await callback.message.reply(f"Bot Stats:\nUsers: {user_count}\nTotal USDT: {total_usdt or 0:,.2f}\nTotal TRX: {total_trx or 0:,.2f}\nTotal BNB: {total_bnb or 0:,.6f}\nTotal DOGE: {total_doge or 0:,.2f}\nTotal TON: {total_ton or 0:,.2f}")
    await callback.answer()

@dispatcher.callback_query(F.data == "add_admin")
async def process_add_admin(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.username.lower() not in ["coinstakebot_admin", "tyhi87655"]:
        await callback.answer("Only the main admins (@CoinStakeBot_Admin or @Tyhi87655) can add admins!")
        return
    await callback.message.reply("Please enter the user ID you want to add as an admin:")
    await state.set_state(AdminState.waiting_for_add_admin_id)
    await callback.answer()

@dispatcher.message(AdminState.waiting_for_add_admin_id)
async def add_admin_id(message: types.Message, state: FSMContext):
    try:
        new_admin_id = int(message.text)
        conn = await db_connect()
        if conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (new_admin_id,))
            conn.commit()
            conn.close()
            await message.reply(f"User with ID {new_admin_id} has been added as an admin!")
        await state.clear()
    except ValueError:
        await message.reply("Invalid ID. Please enter a number.")

@dispatcher.callback_query(F.data == "remove_admin")
async def process_remove_admin(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.username.lower() not in ["coinstakebot_admin", "tyhi87655"]:
        await callback.answer("Only the main admins (@CoinStakeBot_Admin or @Tyhi87655) can remove admins!")
        return
    
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM admins WHERE user_id != 363541134")
        admins = cursor.fetchall()
        conn.close()
        
        if not admins:
            await callback.message.reply("No other admins exist!")
            await callback.answer()
            return
        
        remove_menu = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Remove {admin[0]}", callback_data=f"remove_{admin[0]}")] for admin in admins
        ])
        await callback.message.reply("Admins to remove:", reply_markup=remove_menu)
    await callback.answer()

@dispatcher.callback_query(F.data.startswith("remove_"))
async def confirm_remove_admin(callback: types.CallbackQuery):
    admin_id = int(callback.data.split("_")[1])
    conn = await db_connect()
    if conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM admins WHERE user_id = ?", (admin_id,))
        conn.commit()
        conn.close()
        await callback.message.reply(f"Admin with ID {admin_id} has been removed!")
    await callback.answer()

@dispatcher.message()
async def handle_invalid(message: types.Message):
    await message.reply("Please choose an option from the menu.", reply_markup=main_menu)

async def main():
    logging.info("Starting bot...")
    await initialize_database()
    asyncio.create_task(schedule_reports())
    webhook_url = "https://new-staking-bot.onrender.com/telegram-webhook"  # آدرس سرور Render
    await bot.set_webhook(webhook_url)
    logging.info(f"Webhook set to {webhook_url}")

async def run_web():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 10000)))  # پورت 10000 که Render تشخیص داده
    await site.start()
    logging.info("Web server started.")

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