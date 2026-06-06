import asyncio
import logging
import random
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import ADMIN_IDS, BOT_TOKEN, REQUIRED_CHANNEL
from database import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()


# ── FSM States ───────────────────────────────────────────────────────────────

class CreateGiveaway(StatesGroup):
    title       = State()
    description = State()
    prize       = State()
    end_date    = State()


# ── Subscription check ───────────────────────────────────────────────────────

async def is_subscribed(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        return member.status not in ("left", "kicked", "banned")
    except Exception:
        return False


def subscribe_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")],
    ])


# ── Keyboards ─────────────────────────────────────────────────────────────────

def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Активные розыгрыши", callback_data="list_giveaways")],
        [InlineKeyboardButton(text="📊 Мои участия",        callback_data="my_entries")],
    ])


def admin_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать розыгрыш",    callback_data="create_giveaway")],
        [InlineKeyboardButton(text="📋 Все розыгрыши",       callback_data="admin_giveaways")],
        [InlineKeyboardButton(text="👥 Статистика участников", callback_data="admin_users_stat")],
        [InlineKeyboardButton(text="🏆 Выбрать победителя",  callback_data="pick_winner")],
    ])


def giveaway_kb_with_ref(giveaway_id: int, bot_username: str, user_id: int):
    share_url = f"https://t.me/{bot_username}?start=ref_{giveaway_id}_{user_id}"
    share_text = "🎁 Участвую в крутом розыгрыше! Присоединяйся!"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Участвовать",         callback_data=f"join_{giveaway_id}")],
        [InlineKeyboardButton(text="🔗 Поделиться (+1 шанс)", url=f"https://t.me/share/url?url={share_url}&text={share_text}")],
        [InlineKeyboardButton(text="📊 Мои шансы",           callback_data=f"my_chances_{giveaway_id}")],
    ])


def back_kb(target="back_main"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=target)]
    ])


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def get_bot_username(bot: Bot) -> str:
    me = await bot.get_me()
    return me.username


def format_giveaway(g: dict) -> str:
    status = "🟢 Активен" if g["is_active"] else "🔴 Завершён"
    return (
        f"🎁 <b>{g['title']}</b>\n\n"
        f"📝 {g['description']}\n\n"
        f"🏆 Приз: <b>{g['prize']}</b>\n"
        f"📅 До: {g['end_date']}\n"
        f"👥 Участников: {g['participants_count']}\n"
        f"🎟 Всего билетов: {g['total_tickets']}\n"
        f"Статус: {status}"
    )


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    args     = message.text.split()
    user_id  = message.from_user.id
    username = message.from_user.username or message.from_user.full_name

    db.add_user(user_id, username)

    # Handle referral: /start ref_{giveaway_id}_{referrer_id}
    if len(args) > 1 and args[1].startswith("ref_"):
        parts = args[1].split("_")
        if len(parts) == 3:
            try:
                giveaway_id = int(parts[1])
                referrer_id = int(parts[2])
                if referrer_id != user_id:
                    db.track_invite(referrer_id, user_id)
                    credited = db.credit_share(giveaway_id, referrer_id, user_id)
                    if credited:
                        try:
                            await bot.send_message(
                                referrer_id,
                                "🎉 Кто-то перешёл по вашей ссылке!\n➕ +1 шанс в розыгрыше начислен!"
                            )
                        except Exception:
                            pass
            except (ValueError, IndexError):
                pass

    # Check subscription
    if await is_subscribed(bot, user_id):
        greeting = (
            f"👋 Привет, <b>{message.from_user.full_name}</b>!\n\n"
            f"🎁 Добро пожаловать в бот розыгрышей!\n"
            f"Участвуй, делись с друзьями и увеличивай свои шансы на победу!"
        )
        await message.answer(greeting, parse_mode="HTML", reply_markup=main_menu_kb())
        if is_admin(user_id):
            await message.answer("🔐 <b>Панель администратора</b>", parse_mode="HTML", reply_markup=admin_menu_kb())
    else:
        await message.answer(
            f"👋 Привет, <b>{message.from_user.full_name}</b>!\n\n"
            f"📢 Для участия в розыгрышах необходимо подписаться на наш канал.\n"
            f"Подпишись и нажми «✅ Я подписался»!",
            parse_mode="HTML",
            reply_markup=subscribe_kb()
        )


# ── /admin ────────────────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа.")
        return
    await message.answer("🔐 <b>Панель администратора</b>", parse_mode="HTML", reply_markup=admin_menu_kb())


# ── Check subscription callback ───────────────────────────────────────────────

@router.callback_query(F.data == "check_sub")
async def check_sub(callback: CallbackQuery, bot: Bot):
    if await is_subscribed(bot, callback.from_user.id):
        await callback.message.edit_text(
            "✅ Подписка подтверждена! Теперь вы можете участвовать в розыгрышах.",
            reply_markup=main_menu_kb()
        )
        if is_admin(callback.from_user.id):
            await callback.message.answer(
                "🔐 <b>Панель администратора</b>", parse_mode="HTML", reply_markup=admin_menu_kb()
            )
    else:
        await callback.answer("❌ Вы ещё не подписались на канал!", show_alert=True)


# ── List giveaways ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "list_giveaways")
async def list_giveaways(callback: CallbackQuery, bot: Bot):
    giveaways = db.get_active_giveaways()
    if not giveaways:
        await callback.message.edit_text("😔 Активных розыгрышей пока нет.", reply_markup=back_kb())
        return

    bot_username = await get_bot_username(bot)
    for g in giveaways:
        kb = giveaway_kb_with_ref(g["id"], bot_username, callback.from_user.id)
        await callback.message.answer(format_giveaway(g), parse_mode="HTML", reply_markup=kb)

    await callback.message.edit_text("📋 Актуальные розыгрыши:", reply_markup=back_kb())
    await callback.answer()


# ── Join giveaway ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("join_"))
async def join_giveaway(callback: CallbackQuery, bot: Bot):
    user_id     = callback.from_user.id
    giveaway_id = int(callback.data.split("_")[1])

    # Check subscription first
    if not await is_subscribed(bot, user_id):
        await callback.message.answer(
            "❌ <b>Для участия необходимо подписаться на канал!</b>\n\n"
            "После подписки нажмите «✅ Я подписался»",
            parse_mode="HTML",
            reply_markup=subscribe_kb()
        )
        await callback.answer()
        return

    result = db.join_giveaway(giveaway_id, user_id)

    if result == "already":
        await callback.answer("✅ Вы уже участвуете в этом розыгрыше!", show_alert=True)
    elif result == "joined":
        tickets = db.get_user_tickets(giveaway_id, user_id)
        await callback.answer(
            f"🎉 Вы успешно зарегистрированы!\n"
            f"🎟 Ваших билетов: {tickets}\n"
            f"Делитесь ссылкой, чтобы получить больше шансов!",
            show_alert=True
        )
    else:
        await callback.answer("❌ Розыгрыш не найден или завершён.", show_alert=True)


# ── My chances ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("my_chances_"))
async def my_chances(callback: CallbackQuery):
    giveaway_id = int(callback.data.split("_")[2])
    user_id     = callback.from_user.id
    tickets     = db.get_user_tickets(giveaway_id, user_id)
    total       = db.get_total_tickets(giveaway_id)
    shares      = db.get_user_shares(giveaway_id, user_id)

    if tickets == 0:
        await callback.answer("Вы не участвуете в этом розыгрыше.", show_alert=True)
        return

    chance = round(tickets / total * 100, 1) if total > 0 else 0
    await callback.answer(
        f"🎟 Ваши билеты: {tickets}\n"
        f"📤 Рефералов: {shares}\n"
        f"📊 Ваш шанс: {chance}%\n"
        f"👥 Всего билетов: {total}",
        show_alert=True
    )


# ── My entries ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "my_entries")
async def my_entries(callback: CallbackQuery):
    entries = db.get_user_entries(callback.from_user.id)
    if not entries:
        await callback.message.edit_text(
            "😔 Вы пока не участвуете ни в одном розыгрыше.",
            reply_markup=back_kb()
        )
        return

    text = "📊 <b>Ваши участия:</b>\n\n"
    for e in entries:
        status = "🟢" if e["is_active"] else "🔴"
        chance = round(e["tickets"] / e["total_tickets"] * 100, 1) if e["total_tickets"] > 0 else 0
        text += (
            f"{status} <b>{e['title']}</b>\n"
            f"   🎟 Билетов: {e['tickets']} | 📊 Шанс: {chance}%\n"
            f"   📤 Рефералов: {e['shares']}\n\n"
        )

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb())
    await callback.answer()


# ── Back ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery):
    await callback.message.edit_text("🏠 Главное меню", reply_markup=main_menu_kb())
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

# ── Create giveaway FSM ───────────────────────────────────────────────────────

@router.callback_query(F.data == "create_giveaway")
async def create_giveaway_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    await state.set_state(CreateGiveaway.title)
    await callback.message.answer("✏️ Введите <b>название</b> розыгрыша:", parse_mode="HTML")
    await callback.answer()


@router.message(CreateGiveaway.title)
async def giveaway_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(CreateGiveaway.description)
    await message.answer("📝 Введите <b>описание</b> розыгрыша:", parse_mode="HTML")


@router.message(CreateGiveaway.description)
async def giveaway_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(CreateGiveaway.prize)
    await message.answer("🏆 Введите <b>приз</b> победителя:", parse_mode="HTML")


@router.message(CreateGiveaway.prize)
async def giveaway_prize(message: Message, state: FSMContext):
    await state.update_data(prize=message.text)
    await state.set_state(CreateGiveaway.end_date)
    await message.answer("📅 Введите дату окончания в формате <b>ДД.ММ.ГГГГ</b>:", parse_mode="HTML")


@router.message(CreateGiveaway.end_date)
async def giveaway_end_date(message: Message, state: FSMContext):
    try:
        datetime.strptime(message.text, "%d.%m.%Y")
    except ValueError:
        await message.answer("❌ Неверный формат. Введите ДД.ММ.ГГГГ:")
        return

    data = await state.get_data()
    giveaway_id = db.create_giveaway(
        title=data["title"], description=data["description"],
        prize=data["prize"], end_date=message.text,
        created_by=message.from_user.id
    )
    await state.clear()
    await message.answer(
        f"✅ Розыгрыш <b>«{data['title']}»</b> создан! ID: <code>{giveaway_id}</code>",
        parse_mode="HTML", reply_markup=admin_menu_kb()
    )


# ── Admin: all giveaways ──────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_giveaways")
async def admin_giveaways(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    giveaways = db.get_all_giveaways()
    if not giveaways:
        await callback.message.edit_text("📋 Розыгрышей пока нет.", reply_markup=admin_menu_kb())
        return

    text = "📋 <b>Все розыгрыши:</b>\n\n"
    buttons = []
    for g in giveaways:
        s = "🟢" if g["is_active"] else "🔴"
        text += (
            f"{s} [ID:{g['id']}] <b>{g['title']}</b>\n"
            f"   👥 {g['participants_count']} уч. | 🎟 {g['total_tickets']} билетов | до {g['end_date']}\n\n"
        )
        buttons.append([InlineKeyboardButton(
            text=f"{s} {g['title']}", callback_data=f"admin_giveaway_{g['id']}"
        )])

    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")])
    await callback.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_giveaway_"))
async def admin_giveaway_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    giveaway_id = int(callback.data.split("_")[2])
    g = db.get_giveaway(giveaway_id)
    if not g:
        await callback.answer("Розыгрыш не найден.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Выбрать победителя",   callback_data=f"pick_{giveaway_id}")],
        [InlineKeyboardButton(text="📊 Статистика рефералов", callback_data=f"shares_stat_{giveaway_id}")],
        [InlineKeyboardButton(text="📢 Опубликовать в канале", callback_data=f"post_{giveaway_id}")],
        [InlineKeyboardButton(
            text="🔴 Завершить" if g["is_active"] else "🟢 Активировать",
            callback_data=f"toggle_{giveaway_id}"
        )],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_giveaways")],
    ])
    await callback.message.edit_text(format_giveaway(g), parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_"))
async def toggle_giveaway(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    giveaway_id = int(callback.data.split("_")[1])
    new_status  = db.toggle_giveaway(giveaway_id)
    await callback.answer(f"Розыгрыш {'активирован 🟢' if new_status else 'завершён 🔴'}", show_alert=True)
    # Refresh detail view
    callback.data = f"admin_giveaway_{giveaway_id}"
    await admin_giveaway_detail(callback)


# ── Admin: post giveaway to channel ──────────────────────────────────────────

@router.callback_query(F.data.startswith("post_"))
async def post_to_channel(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    giveaway_id = int(callback.data.split("_")[1])
    g = db.get_giveaway(giveaway_id)
    if not g:
        await callback.answer("Розыгрыш не найден.", show_alert=True)
        return

    post = (
        f"🎁 <b>{g['title']}</b>\n\n"
        f"📝 {g['description']}\n\n"
        f"🏆 Приз: <b>{g['prize']}</b>\n"
        f"📅 Окончание: {g['end_date']}\n\n"
        f"👇 Чтобы участвовать — нажми кнопку ниже!"
    )

    bot_username = await get_bot_username(bot)
    join_url = f"https://t.me/{bot_username}"

    channel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Участвовать в розыгрыше", url=join_url)],
    ])

    try:
        await bot.send_message(
            chat_id=REQUIRED_CHANNEL,
            text=post,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=channel_kb
        )
        await callback.answer("✅ Пост опубликован в канале!", show_alert=True)
    except Exception:
        await callback.answer("❌ Ошибка: бот не может писать в канал. Добавьте бота в админы канала.", show_alert=True)


# ── Admin: global users stats ─────────────────────────────────────────────────

@router.callback_query(F.data == "admin_users_stat")
async def admin_users_stat(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    total_users   = db.get_total_users()
    total_invites = db.get_total_invites()
    top_inviters  = db.get_top_inviters(limit=10)
    top_sharers   = db.get_top_sharers_global(limit=10)

    text = (
        f"👥 <b>Статистика пользователей</b>\n\n"
        f"📊 Всего пользователей: <b>{total_users}</b>\n"
        f"📨 Всего приглашений: <b>{total_invites}</b>\n\n"
    )

    text += "🏅 <b>Топ по приглашённым пользователям:</b>\n"
    if top_inviters:
        medals = ["🥇","🥈","🥉"]
        for i, u in enumerate(top_inviters):
            m    = medals[i] if i < 3 else f"{i+1}."
            name = f"@{u['username']}" if u.get("username") else f"ID:{u['user_id']}"
            text += f"  {m} {name} — {u['invite_count']} чел.\n"
    else:
        text += "  Нет данных\n"

    text += "\n🔗 <b>Топ по рефералам (все розыгрыши):</b>\n"
    if top_sharers:
        medals = ["🥇","🥈","🥉"]
        for i, u in enumerate(top_sharers):
            m    = medals[i] if i < 3 else f"{i+1}."
            name = f"@{u['username']}" if u.get("username") else f"ID:{u['user_id']}"
            text += f"  {m} {name} — {u['total_shares']} рефералов\n"
    else:
        text += "  Нет данных\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Рефералы по розыгрышам", callback_data="admin_shares")],
        [InlineKeyboardButton(text="◀️ Назад",              callback_data="admin_back")],
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


# ── Admin: shares stats per giveaway ─────────────────────────────────────────

@router.callback_query(F.data == "admin_shares")
async def admin_shares(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    giveaways = db.get_all_giveaways()
    if not giveaways:
        await callback.message.edit_text("Нет розыгрышей.", reply_markup=admin_menu_kb())
        return

    buttons = [[InlineKeyboardButton(
        text=f"{'🟢' if g['is_active'] else '🔴'} {g['title']}",
        callback_data=f"shares_stat_{g['id']}"
    )] for g in giveaways]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_users_stat")])

    await callback.message.edit_text(
        "📊 Выберите розыгрыш для статистики рефералов:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shares_stat_"))
async def shares_stat(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    giveaway_id = int(callback.data.split("_")[2])
    g           = db.get_giveaway(giveaway_id)
    stats       = db.get_shares_leaderboard(giveaway_id)

    text = (
        f"📊 <b>Статистика рефералов — {g['title']}</b>\n"
        f"👥 Участников: {g['participants_count']} | 🎟 Билетов: {g['total_tickets']}\n\n"
    )

    if not stats:
        text += "Рефералов пока нет."
    else:
        medals = ["🥇","🥈","🥉"]
        for i, s in enumerate(stats):
            m       = medals[i] if i < 3 else f"{i+1}."
            name    = f"@{s['username']}" if s.get("username") else f"ID:{s['user_id']}"
            tickets = s["base_tickets"] + s["share_tickets"]
            text   += (
                f"{m} {name}\n"
                f"   📤 Рефералов: {s['shares']} | 🎟 Билетов: {tickets}\n"
            )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_shares")]
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


# ── Admin: pick winner ────────────────────────────────────────────────────────

@router.callback_query(F.data == "pick_winner")
async def pick_winner_select(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    giveaways = db.get_active_giveaways()
    if not giveaways:
        await callback.message.edit_text("Нет активных розыгрышей.", reply_markup=admin_menu_kb())
        return

    buttons = [[InlineKeyboardButton(text=f"🏆 {g['title']}", callback_data=f"pick_{g['id']}")]
               for g in giveaways]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")])

    await callback.message.edit_text(
        "🏆 Выберите розыгрыш для определения победителя:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pick_"))
async def pick_winner(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    giveaway_id  = int(callback.data.split("_")[1])
    g            = db.get_giveaway(giveaway_id)
    if not g:
        await callback.answer("Розыгрыш не найден.", show_alert=True)
        return

    participants = db.get_participants_with_tickets(giveaway_id)
    if not participants:
        await callback.answer("Нет участников!", show_alert=True)
        return

    pool = []
    for p in participants:
        pool.extend([p["user_id"]] * p["tickets"])

    winner_id     = random.choice(pool)
    winner        = db.get_user(winner_id)
    winner_tickets = next(p["tickets"] for p in participants if p["user_id"] == winner_id)
    winner_shares  = db.get_user_shares(giveaway_id, winner_id)

    try:
        await bot.send_message(
            winner_id,
            f"🎉🏆 <b>Поздравляем! Вы победили!</b>\n\n"
            f"🎁 Розыгрыш: <b>{g['title']}</b>\n"
            f"🏆 Приз: <b>{g['prize']}</b>\n\n"
            f"С вами свяжется администратор для получения приза!",
            parse_mode="HTML"
        )
    except Exception:
        pass

    db.set_winner(giveaway_id, winner_id)

    winner_name = f"@{winner['username']}" if winner and winner.get("username") else f"ID:{winner_id}"
    await callback.message.answer(
        f"🏆 <b>Победитель определён!</b>\n\n"
        f"🎁 Розыгрыш: <b>{g['title']}</b>\n"
        f"👤 Победитель: {winner_name}\n"
        f"🎟 Билетов было: {winner_tickets}\n"
        f"📤 Рефералов: {winner_shares}\n"
        f"👥 Всего участников: {g['participants_count']}\n"
        f"🎫 Всего билетов в пуле: {len(pool)}",
        parse_mode="HTML", reply_markup=admin_menu_kb()
    )
    await callback.answer()


# ── Admin back ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    await callback.message.edit_text(
        "🔐 <b>Панель администратора</b>", parse_mode="HTML", reply_markup=admin_menu_kb()
    )
    await callback.answer()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info("Bot started!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
