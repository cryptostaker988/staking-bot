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

    user_id = int(data.get("order_id"))
    amount = data.get("actually_paid") or data.get("pay_amount") or data.get("price_amount")
    if amount is None:
        logging.error("No valid amount found in webhook data.")
        return web.Response(text="No amount provided", status=400)
    
    amount = float(amount)
    currency = data.get("pay_currency", "").upper()
    if currency == "USDTTRC20":
        currency = "USDT"
    elif currency == "BSC_BNB":
        currency = "BNB"

    min_deposit = await get_min_deposit(currency)
    if amount < min_deposit:
        credited_amount = amount * 0.9
        await update_balance(user_id, credited_amount, currency)
        await add_transaction(user_id, "deposit", credited_amount, currency)
        await bot.send_message(user_id, f"Your deposit of {amount:.2f} {currency} was below the minimum ({min_deposit} {currency}). Due to a 10% fee, {credited_amount:.2f} {currency} has been credited!")
        user = await get_user(user_id)
        if user and user[12]:
            referrer_id = user[12]
            await bot.send_message(referrer_id, f"Because your referral (user {user_id}) deposited {amount:.2f} {currency}, which is less than the minimum ({min_deposit} {currency}), no referral bonus was credited.")
    else:
        credited_amount = amount
        await update_balance(user_id, amount, currency)
        await add_transaction(user_id, "deposit", amount, currency)
        await bot.send_message(user_id, f"Your deposit of {amount:.2f} {currency} has been credited!")
        user = await get_user(user_id)
        if user and user[12]:
            referrer_id = user[12]
            bonus_amount = credited_amount * 0.05
            await update_balance(referrer_id, bonus_amount, currency)
            await add_transaction(referrer_id, "referral_bonus", bonus_amount, currency)
            await bot.send_message(referrer_id, f"Your balance has been increased by {bonus_amount:.2f} {currency} as a referral bonus from user {user_id}.")

    return web.Response(text="Success")

app.router.add_post('/webhook', handle_webhook)

@dispatcher.message(Command("start"))
async def send_welcome(message: types.Message):
    global ADMIN_ID
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    command_parts = message.text.split()
    referrer_id = int(command_parts[1]) if len(command_parts) > 1 and command_parts[1].isdigit() else None
    
    await add_user(user_id, username, referrer_id)
    if username.lower() in ["coinstakebot_admin", "tyhi87655", "kanka1"]:
        ADMIN_ID = user_id
        logging.info(f"Admin ID set to: {ADMIN_ID}")
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
         InlineKeyboardButton(text="Edit Deposit Limits", callback_data="edit_deposit_limits")]
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
    await message.reply(f"Your balance:\n{balance_usdt:,.2f} USDT\n{balance_trx:,.2f} TRX\n{balance_bnb:,.4f} BNB\n{balance_doge:,.2f} DOGE\n{balance_ton:,.2f} TON")

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
        
        response += f"- {plan_desc}: {amount:,.2f} {currency} (Started: {start_date})\n"
    await message.reply(response)

@dispatcher.message(Command("viewearnings"))
async def view_earnings_command(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if user:
        earnings_usdt, earnings_trx, earnings_bnb, earnings_doge, earnings_ton = await calculate_total_earnings(user_id)
        await message.reply(f"Your total earnings:\n{earnings_usdt:,.2f} USDT\n{earnings_trx:,.2f} TRX\n{earnings_bnb:,.4f} BNB\n{earnings_doge:,.2f} DOGE\n{earnings_ton:,.2f} TON", reply_markup=earnings_menu)
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
            await message.reply(f"Minimum deposit for {currency} is {min_deposit} {currency}. Please enter a higher amount.", reply_markup=main_menu)
            return
        
        address = await generate_payment_address(user_id, amount, currency)
        if address:
            await save_deposit_address(user_id, currency, address)
            network = "TRC-20" if currency in ["USDT", "TRX"] else "BEP-20" if currency == "BNB" else "Main Network"
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
            await message.reply(f"Amount must be at least {min_stake} {currency} for {plan_names[plan_id]}.", reply_markup=stake_plan_menu)
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
        await message.reply(f"Please enter the amount you want to transfer to your balance:\nAvailable:\n{earnings_usdt:,.2f} USDT\n{earnings_trx:,.2f} TRX\n{earnings_bnb:,.4f} BNB\n{earnings_doge:,.2f} DOGE\n{earnings_ton:,.2f} TON\nSpecify currency (e.g., '10 TRX' or '5 USDT'):", reply_markup=earnings_menu)
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
            await message.reply(f"Amount must be at least {min_withdrawal} {currency}.", reply_markup=main_menu)
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
            await message.reply(f"The network fee for withdrawing {currency} is {fee:.4f} {currency}. Please enter your {network} {currency} wallet address:", reply_markup=main_menu)
            await state.set_state(WithdrawState.entering_new_address)
            return
        
        if await update_earnings(user_id, -total_amount, currency):
            await add_withdraw_request(user_id, amount, currency, fee, wallet_address)
            network = "TRC-20" if currency in ["USDT", "TRX"] else "BEP-20" if currency == "BNB" else "Main Network"
            await message.reply(f"The network fee for withdrawing {currency} is {fee:.4f} {currency}. {amount:,.2f} {currency} has been deducted from your earnings (including fee) and will be transferred to your {network} wallet ({wallet_address}) within 24 hours after review.",
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
    min_withdrawal = await get_min_withdrawal(currency)
    await message.reply(f"Network fee for withdrawing {currency} is {fee:.4f} {currency}. Enter the amount to withdraw (minimum {min_withdrawal} {currency}):",
                       reply_markup=main_menu)
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
        await callback.message.reply(f"Bot Stats:\nUsers: {user_count}\nTotal USDT: {total_usdt or 0:,.2f}\nTotal TRX: {total_trx or 0:,.2f}\nTotal BNB: {total_bnb or 0:,.4f}\nTotal DOGE: {total_doge or 0:,.2f}\nTotal TON: {total_ton or 0:,.2f}")
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
    await dispatcher.start_polling(bot)
    logging.info("Bot started polling.")

async def run_web():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8000)))
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