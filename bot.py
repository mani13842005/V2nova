import asyncio
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.environ.get("ADMIN_IDS", "").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/store.db")
CURRENCY = os.environ.get("CURRENCY", "تومان")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")
try:
    ADMIN_IDS = {int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip()}
except ValueError:
    raise RuntimeError("ADMIN_IDS must be comma-separated Telegram numeric user IDs")

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("v2ray-store")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

USER_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🛍 پلن‌ها"), KeyboardButton(text="👤 حساب من")],
        [KeyboardButton(text="➕ درخواست شارژ"), KeyboardButton(text="📦 سفارش‌های من")],
        [KeyboardButton(text="ℹ️ راهنما")]
    ],
    resize_keyboard=True
)

def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          user_id INTEGER PRIMARY KEY,
          username TEXT,
          first_name TEXT,
          balance INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS plans(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          price INTEGER NOT NULL CHECK(price > 0),
          traffic_gb INTEGER NOT NULL CHECK(traffic_gb > 0),
          days INTEGER NOT NULL CHECK(days > 0),
          active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS configs(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          plan_id INTEGER NOT NULL REFERENCES plans(id),
          config TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL DEFAULT 'available'
        );
        CREATE TABLE IF NOT EXISTS orders(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(user_id),
          plan_id INTEGER NOT NULL REFERENCES plans(id),
          config_id INTEGER REFERENCES configs(id),
          amount INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'delivered',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS topups(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(user_id),
          amount INTEGER NOT NULL CHECK(amount > 0),
          note TEXT,
          status TEXT NOT NULL DEFAULT 'pending',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS ledger(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(user_id),
          amount INTEGER NOT NULL,
          reason TEXT NOT NULL,
          ref TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)
        if c.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0:
            c.executemany(
                "INSERT INTO plans(name,price,traffic_gb,days) VALUES(?,?,?,?)",
                [
                    ("اقتصادی", 150000, 30, 30),
                    ("استاندارد", 250000, 80, 30),
                    ("حرفه‌ای", 450000, 200, 60),
                ],
            )

def ensure_user(user):
    with db() as c:
        c.execute("""INSERT INTO users(user_id,username,first_name) VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name""",
        (user.id, user.username, user.first_name or ""))

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def money(value):
    return f"{int(value):,} {CURRENCY}"

def main_menu_text():
    return "به فروشگاه کانفیگ خوش آمدید.\nاز دکمه‌های زیر برای دیدن پلن‌ها، کیف پول و سفارش‌ها استفاده کنید."

def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in rows
    ])

def plans_keyboard():
    with db() as c:
        plans = c.execute("SELECT * FROM plans WHERE active=1 ORDER BY price").fetchall()
    rows = []
    for p in plans:
        rows.append([(f"{p['name']} — {money(p['price'])}", f"plan:{p['id']}")])
    rows.append([("🏠 منوی اصلی", "home")])
    return kb(rows)

@dp.message(CommandStart())
async def start(message: Message):
    ensure_user(message.from_user)
    await message.answer(main_menu_text(), reply_markup=USER_MENU)

@dp.message(Command("help"))
@dp.message(F.text == "ℹ️ راهنما")
async def help_message(message: Message):
    text = (
        "راهنما:\n"
        "• از «پلن‌ها» پلن موردنظر را انتخاب کنید.\n"
        "• خرید از موجودی کیف پول کم می‌کند.\n"
        "• اگر موجودی کافی نباشد، خرید انجام نمی‌شود.\n"
        "• برای شارژ کیف پول، درخواست ثبت کنید و منتظر بررسی مدیر بمانید.\n"
        "• کانفیگ تنها زمانی خودکار تحویل می‌شود که مدیر قبلاً کانفیگ آماده همان پلن را در موجودی قرار داده باشد.\n"
        "• پشتیبانی: با مدیر ربات تماس بگیرید."
    )
    await message.answer(text, reply_markup=USER_MENU)

@dp.message(F.text == "🛍 پلن‌ها")
async def show_plans(message: Message):
    ensure_user(message.from_user)
    await message.answer("یکی از پلن‌های موجود را انتخاب کنید:", reply_markup=plans_keyboard())

@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery):
    await callback.message.answer(main_menu_text(), reply_markup=USER_MENU)
    await callback.answer()

@dp.callback_query(F.data.startswith("plan:"))
async def show_plan(callback: CallbackQuery):
    try:
        plan_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    with db() as c:
        p = c.execute("SELECT * FROM plans WHERE id=? AND active=1", (plan_id,)).fetchone()
        available = c.execute("SELECT COUNT(*) FROM configs WHERE plan_id=? AND status='available'", (plan_id,)).fetchone()[0]
        user = c.execute("SELECT balance FROM users WHERE user_id=?", (callback.from_user.id,)).fetchone()
    if not p:
        await callback.answer("این پلن فعال نیست.", show_alert=True)
        return
    balance = user["balance"] if user else 0
    text = (
        f"📦 {p['name']}\n"
        f"💰 قیمت: {money(p['price'])}\n"
        f"📊 حجم: {p['traffic_gb']} گیگابایت\n"
        f"🗓 اعتبار: {p['days']} روز\n"
        f"📡 موجودی آماده: {available} عدد\n"
        f"👛 موجودی کیف پول شما: {money(balance)}\n\n"
        "در صورت موجود بودن کانفیگ و کافی بودن موجودی، خرید و تحویل به‌صورت خودکار انجام می‌شود."
    )
    await callback.message.answer(text, reply_markup=kb([
        [("✅ خرید این پلن", f"buy:{plan_id}")],
        [("⬅️ بازگشت به پلن‌ها", "plans"), ("🏠 منوی اصلی", "home")]
    ]))
    await callback.answer()

@dp.callback_query(F.data == "plans")
async def back_plans(callback: CallbackQuery):
    await callback.message.answer("پلن موردنظر را انتخاب کنید:", reply_markup=plans_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("buy:"))
async def buy_plan(callback: CallbackQuery):
    try:
        plan_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    user_id = callback.from_user.id
    result = None
    with db() as c:
        # BEGIN IMMEDIATE serializes inventory/charge operations to avoid double-selling.
        c.execute("BEGIN IMMEDIATE")
        user = c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        plan = c.execute("SELECT * FROM plans WHERE id=? AND active=1", (plan_id,)).fetchone()
        if not user or not plan:
            result = ("error", "کاربر یا پلن پیدا نشد.")
        elif user["balance"] < plan["price"]:
            result = ("error", f"موجودی کافی نیست.\nموجودی فعلی: {money(user['balance'])}\nقیمت پلن: {money(plan['price'])}")
        else:
            config = c.execute("SELECT id,config FROM configs WHERE plan_id=? AND status='available' ORDER BY id LIMIT 1", (plan_id,)).fetchone()
            if not config:
                result = ("error", "موجودی کانفیگ این پلن تمام شده است. بعداً دوباره امتحان کنید.")
            else:
                c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (plan["price"], user_id))
                c.execute("UPDATE configs SET status='sold' WHERE id=? AND status='available'", (config["id"],))
                if c.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RuntimeError("Config inventory race detected")
                cur = c.execute("INSERT INTO orders(user_id,plan_id,config_id,amount,status) VALUES(?,?,?,?,?)",
                                (user_id, plan_id, config["id"], plan["price"], "delivered"))
                order_id = cur.lastrowid
                c.execute("INSERT INTO ledger(user_id,amount,reason,ref) VALUES(?,?,?,?)",
                          (user_id, -plan["price"], "خرید پلن", str(order_id)))
                result = ("ok", (order_id, plan, config["config"], plan["price"]))
    if result[0] == "error":
        await callback.answer(result[1], show_alert=True)
    else:
        order_id, plan, config_text, amount = result[1]
        await callback.message.answer(
            f"✅ خرید موفق!\nسفارش: #{order_id}\nپلن: {plan['name']}\nمبلغ: {money(amount)}\n\n"
            f"کانفیگ شما:\n<code>{escape_html(config_text)}</code>",
            parse_mode="HTML",
            reply_markup=USER_MENU
        )
        await callback.answer("خرید انجام شد.")

def escape_html(value):
    return (value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

@dp.message(F.text == "👤 حساب من")
async def account(message: Message):
    ensure_user(message.from_user)
    with db() as c:
        u = c.execute("SELECT balance,created_at FROM users WHERE user_id=?", (message.from_user.id,)).fetchone()
        count = c.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (message.from_user.id,)).fetchone()[0]
    await message.answer(f"👤 حساب شما\nشناسه تلگرام: <code>{message.from_user.id}</code>\n"
                         f"💰 موجودی: {money(u['balance'])}\n📦 تعداد خریدها: {count}",
                         parse_mode="HTML", reply_markup=USER_MENU)

@dp.message(F.text == "📦 سفارش‌های من")
async def my_orders(message: Message):
    with db() as c:
        rows = c.execute("""SELECT o.id,o.amount,o.created_at,p.name,p.traffic_gb,p.days
        FROM orders o JOIN plans p ON p.id=o.plan_id WHERE o.user_id=? ORDER BY o.id DESC LIMIT 20""",
        (message.from_user.id,)).fetchall()
    if not rows:
        await message.answer("هنوز سفارشی ثبت نکرده‌اید.", reply_markup=USER_MENU)
        return
    parts = ["📦 آخرین سفارش‌های شما:"]
    for o in rows:
        parts.append(f"#{o['id']} — {o['name']} — {money(o['amount'])}\nحجم {o['traffic_gb']}GB · {o['days']} روز · {o['created_at']}")
    await message.answer("\n\n".join(parts), reply_markup=USER_MENU)

@dp.message(F.text == "➕ درخواست شارژ")
async def topup_start(message: Message):
    await message.answer("برای ثبت درخواست شارژ، این قالب را ارسال کنید:\n<code>/topup 200000 توضیح-اختیاری</code>\n"
                         "مبلغ را به عدد صحیح و به تومان وارد کنید. ثبت درخواست به‌معنی واریز وجه نیست؛ مدیر پس از بررسی، کیف پول را شارژ می‌کند.",
                         parse_mode="HTML", reply_markup=USER_MENU)

@dp.message(Command("topup"))
async def topup(message: Message):
    ensure_user(message.from_user)
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("قالب درست: /topup 200000 توضیح-اختیاری")
        return
    amount = int(parts[1])
    if amount < 10000 or amount > 100000000:
        await message.answer("مبلغ باید بین 10,000 و 100,000,000 تومان باشد.")
        return
    note = parts[2][:300] if len(parts) > 2 else ""
    with db() as c:
        cur = c.execute("INSERT INTO topups(user_id,amount,note) VALUES(?,?,?)", (message.from_user.id, amount, note))
        topup_id = cur.lastrowid
    await message.answer(f"درخواست شارژ #{topup_id} با مبلغ {money(amount)} ثبت شد. پس از بررسی مدیر نتیجه اعلام می‌شود.", reply_markup=USER_MENU)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"➕ درخواست شارژ #{topup_id}\nکاربر: {message.from_user.id} (@{message.from_user.username or 'بدون‌نام'})\nمبلغ: {money(amount)}\nیادداشت: {note or '—'}",
                                   reply_markup=kb([[("تأیید شارژ", f"topupok:{topup_id}"), ("رد درخواست", f"topupno:{topup_id}")]]))
        except Exception:
            log.exception("Could not notify admin %s", admin_id)

@dp.callback_query(F.data.startswith("topupok:"))
async def approve_topup(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    topup_id = int(callback.data.split(":")[1])
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM topups WHERE id=?", (topup_id,)).fetchone()
        if not row or row["status"] != "pending":
            await callback.answer("این درخواست قبلاً بررسی شده یا وجود ندارد.", show_alert=True)
            return
        c.execute("UPDATE topups SET status='approved' WHERE id=? AND status='pending'", (topup_id,))
        c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (row["amount"], row["user_id"]))
        c.execute("INSERT INTO ledger(user_id,amount,reason,ref) VALUES(?,?,?,?)",
                  (row["user_id"], row["amount"], "تأیید درخواست شارژ", str(topup_id)))
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"شارژ درخواست #{topup_id} تأیید شد.")
    try:
        await bot.send_message(row["user_id"], f"✅ درخواست شارژ #{topup_id} تأیید شد.\nمبلغ {money(row['amount'])} به کیف پول شما افزوده شد.")
    except Exception:
        log.exception("Could not notify user")
    await callback.answer("کیف پول شارژ شد.")

@dp.callback_query(F.data.startswith("topupno:"))
async def reject_topup(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    topup_id = int(callback.data.split(":")[1])
    with db() as c:
        row = c.execute("SELECT * FROM topups WHERE id=?", (topup_id,)).fetchone()
        if not row or row["status"] != "pending":
            await callback.answer("این درخواست قبلاً بررسی شده یا وجود ندارد.", show_alert=True)
            return
        c.execute("UPDATE topups SET status='rejected' WHERE id=? AND status='pending'", (topup_id,))
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"درخواست شارژ #{topup_id} رد شد.")
    try:
        await bot.send_message(row["user_id"], f"درخواست شارژ #{topup_id} تأیید نشد. در صورت اشتباه با مدیر تماس بگیرید.")
    except Exception:
        log.exception("Could not notify user")
    await callback.answer("درخواست رد شد.")

# Admin commands
@dp.message(Command("admin"))
async def admin_help(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🛠 دستورات مدیریت:\n"
        "/addplan نام|قیمت|حجمGB|روز — افزودن پلن\n"
        "/plansadmin — فهرست پلن‌ها\n"
        "/toggleplan ID — فعال/غیرفعال کردن پلن\n"
        "/addconfig PLAN_ID — سپس در پیام بعدی یک کانفیگ ارسال کنید\n"
        "/inventory — موجودی کانفیگ‌ها\n"
        "/credit USER_ID AMOUNT — افزودن اعتبار دستی\n"
        "/debit USER_ID AMOUNT — کسر اعتبار دستی\n"
        "/topups — درخواست‌های شارژ اخیر\n"
        "/stats — آمار فروشگاه\n"
        "کانفیگ‌ها را فقط از سرور/پنل خودتان یا با مجوز مدیریت کنید."
    )

@dp.message(Command("addplan"))
async def add_plan(message: Message):
    if not is_admin(message.from_user.id):
        return
    raw = (message.text or "").partition(" ")[2]
    parts = [x.strip() for x in raw.split("|")]
    if len(parts) != 4:
        await message.answer("قالب: /addplan نام پلن|قیمت تومان|حجم GB|روز")
        return
    try:
        name, price, traffic, days = parts[0], int(parts[1]), int(parts[2]), int(parts[3])
        if not name or min(price, traffic, days) <= 0: raise ValueError()
    except ValueError:
        await message.answer("مقادیر نامعتبر است؛ اعداد باید مثبت باشند.")
        return
    with db() as c:
        cur = c.execute("INSERT INTO plans(name,price,traffic_gb,days) VALUES(?,?,?,?)", (name, price, traffic, days))
    await message.answer(f"پلن #{cur.lastrowid} اضافه شد.")

@dp.message(Command("plansadmin"))
async def plans_admin(message: Message):
    if not is_admin(message.from_user.id): return
    with db() as c:
        rows = c.execute("SELECT * FROM plans ORDER BY id").fetchall()
    if not rows:
        await message.answer("پلنی نیست."); return
    await message.answer("\n".join(f"#{p['id']} {p['name']} — {money(p['price'])} — {p['traffic_gb']}GB/{p['days']} روز — {'فعال' if p['active'] else 'غیرفعال'}" for p in rows))

@dp.message(Command("toggleplan"))
async def toggle_plan(message: Message):
    if not is_admin(message.from_user.id): return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("قالب: /toggleplan ID"); return
    with db() as c:
        c.execute("UPDATE plans SET active=1-active WHERE id=?", (int(parts[1]),))
        changed = c.execute("SELECT changes()").fetchone()[0]
    await message.answer("وضعیت پلن تغییر کرد." if changed else "پلن پیدا نشد.")

@dp.message(Command("addconfig"))
async def add_config_prompt(message: Message):
    if not is_admin(message.from_user.id): return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("قالب: /addconfig PLAN_ID"); return
    plan_id = int(parts[1])
    with db() as c:
        p = c.execute("SELECT id,name FROM plans WHERE id=?", (plan_id,)).fetchone()
    if not p:
        await message.answer("پلن پیدا نشد."); return
    # Admin sends the config as the next message, and the bot stores a one-time pending target.
    pending_admin_configs[message.from_user.id] = plan_id
    await message.answer(f"اکنون یک کانفیگ کامل برای پلن «{p['name']}» در پیام بعدی ارسال کنید. آن پیام در موجودی ذخیره می‌شود.")

pending_admin_configs = {}


@dp.message(Command("cancel"))
async def cancel(message: Message):
    pending_admin_configs.pop(message.from_user.id, None)
    await message.answer("عملیات لغو شد.")

@dp.message(Command("inventory"))
async def inventory(message: Message):
    if not is_admin(message.from_user.id): return
    with db() as c:
        rows = c.execute("""SELECT p.id,p.name,
          SUM(CASE WHEN c.status='available' THEN 1 ELSE 0 END) available,
          SUM(CASE WHEN c.status='sold' THEN 1 ELSE 0 END) sold
          FROM plans p LEFT JOIN configs c ON p.id=c.plan_id GROUP BY p.id ORDER BY p.id""").fetchall()
    await message.answer("📡 موجودی کانفیگ‌ها:\n" + "\n".join(f"#{r['id']} {r['name']}: آماده {r['available'] or 0} · فروخته‌شده {r['sold'] or 0}" for r in rows))

@dp.message(Command("credit"))
async def credit(message: Message):
    if not is_admin(message.from_user.id): return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("قالب: /credit USER_ID AMOUNT"); return
    try: user_id, amount = int(parts[1]), int(parts[2])
    except ValueError:
        await message.answer("شناسه و مبلغ باید عدد باشند."); return
    if amount <= 0 or amount > 1000000000:
        await message.answer("مبلغ باید مثبت و در محدوده مجاز باشد."); return
    with db() as c:
        user = c.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not user:
            await message.answer("کاربر ابتدا باید /start را در ربات اجرا کند."); return
        c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id))
        c.execute("INSERT INTO ledger(user_id,amount,reason,ref) VALUES(?,?,?,?)", (user_id, amount, "شارژ دستی مدیر", str(message.from_user.id)))
    await message.answer(f"به کاربر {user_id}، {money(amount)} اعتبار افزوده شد.")
    try: await bot.send_message(user_id, f"مدیر {money(amount)} به کیف پول شما اضافه کرد.")
    except Exception: pass

@dp.message(Command("debit"))
async def debit(message: Message):
    if not is_admin(message.from_user.id): return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("قالب: /debit USER_ID AMOUNT"); return
    try: user_id, amount = int(parts[1]), int(parts[2])
    except ValueError:
        await message.answer("شناسه و مبلغ باید عدد باشند."); return
    if amount <= 0:
        await message.answer("مبلغ باید مثبت باشد."); return
    with db() as c:
        user = c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not user:
            await message.answer("کاربر پیدا نشد."); return
        if user["balance"] < amount:
            await message.answer("موجودی کاربر کمتر از این مبلغ است."); return
        c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, user_id))
        c.execute("INSERT INTO ledger(user_id,amount,reason,ref) VALUES(?,?,?,?)", (user_id, -amount, "کسر دستی مدیر", str(message.from_user.id)))
    await message.answer(f"{money(amount)} از کیف پول {user_id} کم شد.")

@dp.message(Command("topups"))
async def topups_admin(message: Message):
    if not is_admin(message.from_user.id): return
    with db() as c:
        rows = c.execute("SELECT * FROM topups ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        await message.answer("درخواستی ثبت نشده."); return
    await message.answer("\n".join(f"#{r['id']} user={r['user_id']} amount={money(r['amount'])} status={r['status']} note={r['note'] or '-'}" for r in rows))

@dp.message(Command("stats"))
async def stats(message: Message):
    if not is_admin(message.from_user.id): return
    with db() as c:
        users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        orders = c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        sales = c.execute("SELECT COALESCE(SUM(amount),0) FROM orders").fetchone()[0]
        pending = c.execute("SELECT COUNT(*) FROM topups WHERE status='pending'").fetchone()[0]
    await message.answer(f"📊 آمار\nکاربران: {users}\nسفارش‌ها: {orders}\nجمع فروش ثبت‌شده: {money(sales)}\nدرخواست شارژ در انتظار: {pending}")


# Keep this generic text handler last so slash commands and menu handlers run first.
@dp.message(F.text)
async def admin_config_capture(message: Message):
    if message.from_user.id not in pending_admin_configs or not is_admin(message.from_user.id):
        return
    config_text = (message.text or "").strip()
    if not (config_text.startswith(("vless://", "vmess://", "trojan://", "ss://", "https://"))):
        await message.answer("فرمت کانفیگ شناخته‌شده نیست. یک لینک vless://، vmess://، trojan://، ss:// یا لینک اشتراک ارسال کنید.")
        return
    plan_id = pending_admin_configs[message.from_user.id]
    try:
        with db() as c:
            c.execute("INSERT INTO configs(plan_id,config) VALUES(?,?)", (plan_id, config_text))
        pending_admin_configs.pop(message.from_user.id, None)
        await message.answer("✅ کانفیگ به موجودی اضافه شد. برای افزودن کانفیگ دیگر دوباره /addconfig را اجرا کنید.")
    except sqlite3.IntegrityError:
        await message.answer("این کانفیگ قبلاً ثبت شده است. برای لغو افزودن، /cancel را بزنید.")

async def main():
    init_db()
    log.info("Bot polling started; admins=%s DB=%s", sorted(ADMIN_IDS), DB_PATH)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
