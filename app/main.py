import asyncio
import logging
import secrets
import uuid
from datetime import datetime, UTC

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.deep_linking import create_start_link, decode_payload

from app.config import settings
from app.db import init_db
from app.llm import analyze_positions
from app.texts import LANGUAGE_CHOOSER, TEXTS


logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

bot = Bot(settings.bot_token)
dp = Dispatcher()


class IntakeStates(StatesGroup):
    waiting_case_title = State()
    waiting_answers = State()
    waiting_feedback = State()


async def now_iso():
    return datetime.now(UTC).isoformat()


async def get_db():
    return aiosqlite.connect(settings.database_path)


async def get_lang(user_id: int) -> str:
    async with await get_db() as db:
        cursor = await db.execute("SELECT language FROM user_settings WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
    return row[0] if row and row[0] in TEXTS else "ru"


async def set_lang(user_id: int, lang: str):
    async with await get_db() as db:
        await db.execute(
            """
            INSERT INTO user_settings (user_id, language, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET language=excluded.language, updated_at=excluded.updated_at
            """,
            (user_id, lang, await now_iso()),
        )
        await db.commit()


async def t(user_id: int, key: str):
    lang = await get_lang(user_id)
    return TEXTS[lang][key]


async def get_questions(user_id: int):
    lang = await get_lang(user_id)
    return TEXTS[lang]["questions"]


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Русский", callback_data="lang:ru")],
        [InlineKeyboardButton(text="English", callback_data="lang:en")],
    ])


def main_menu_keyboard(texts: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts["menu_create"], callback_data="menu:newcase")],
        [InlineKeyboardButton(text=texts["menu_list"], callback_data="menu:mycases")],
        [InlineKeyboardButton(text=texts["menu_feedback"], callback_data="menu:feedback")],
        [InlineKeyboardButton(text=texts["menu_language"], callback_data="menu:language")],
    ])


async def share_mode_keyboard(user_id: int) -> InlineKeyboardMarkup:
    lang = await get_lang(user_id)
    texts = TEXTS[lang]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts["share_mode_summary"], callback_data="share:summary")],
        [InlineKeyboardButton(text=texts["share_mode_private"], callback_data="share:private")],
        [InlineKeyboardButton(text=texts["share_mode_quote"], callback_data="share:quote")],
    ])


async def feedback_keyboard(user_id: int) -> InlineKeyboardMarkup:
    lang = await get_lang(user_id)
    areas = TEXTS[lang]["feedback_areas"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=areas["questions"], callback_data="feedback_area:questions")],
        [InlineKeyboardButton(text=areas["flow"], callback_data="feedback_area:flow")],
        [InlineKeyboardButton(text=areas["summary"], callback_data="feedback_area:summary")],
        [InlineKeyboardButton(text=areas["ux"], callback_data="feedback_area:ux")],
        [InlineKeyboardButton(text=areas["other"], callback_data="feedback_area:other")],
    ])


async def format_case_header(user_id: int, title: str, conflict_period: str | None = None) -> str:
    lang = await get_lang(user_id)
    texts = TEXTS[lang]
    header = f"{texts['topic']}: {title}"
    if conflict_period:
        header += f"\n{texts['period']}: {conflict_period}"
    return header


@dp.callback_query(F.data.startswith("lang:"))
async def language_selected(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.split(":", 1)[1]
    await set_lang(callback.from_user.id, lang)
    await callback.answer("OK")
    await callback.message.answer(TEXTS[lang]["lang_set"])
    await callback.message.answer(TEXTS[lang]["intro"], reply_markup=main_menu_keyboard(TEXTS[lang]))


@dp.callback_query(F.data.startswith("menu:"))
async def main_menu_action(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":", 1)[1]
    await callback.answer("OK")
    if action == "newcase":
        await new_case(callback.message, state)
    elif action == "mycases":
        await my_cases(callback.message)
    elif action == "feedback":
        await feedback(callback.message, state)
    elif action == "language":
        await callback.message.answer(LANGUAGE_CHOOSER, reply_markup=language_keyboard())


@dp.message(Command("start"))
async def start(message: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    if command.args:
        payload = decode_payload(command.args)
        if payload.startswith("join_"):
            join_code = payload.removeprefix("join_")
            fake_command = CommandObject(prefix="/", command="join", args=join_code)
            await join_case(message, fake_command, state)
            return
    lang = await get_lang(message.from_user.id)
    if lang not in TEXTS or lang == "ru":
        await message.answer(LANGUAGE_CHOOSER, reply_markup=language_keyboard())
    texts = TEXTS[await get_lang(message.from_user.id)]
    await message.answer(await t(message.from_user.id, "intro"), reply_markup=main_menu_keyboard(texts))


@dp.message(Command("newcase"))
async def new_case(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(IntakeStates.waiting_case_title)
    await message.answer(await t(message.from_user.id, "newcase_prompt"))


@dp.message(IntakeStates.waiting_case_title)
async def receive_case_title(message: Message, state: FSMContext):
    case_id = str(uuid.uuid4())
    join_code = secrets.token_hex(3)
    title = message.text.strip()
    created_at = await now_iso()
    async with await get_db() as db:
        await db.execute(
            "INSERT INTO cases (id, creator_user_id, participant_a_user_id, title, join_code, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (case_id, message.from_user.id, message.from_user.id, title, join_code, "waiting_for_b", created_at, created_at),
        )
        await db.commit()
    invite_link = await create_start_link(bot, f"join_{join_code}", encode=True)
    invite_text = (
        f"{await t(message.from_user.id, 'invite_title')}\n\n"
        f"{await format_case_header(message.from_user.id, title)}\n\n"
        f"{await t(message.from_user.id, 'invite_link')}\n{invite_link}\n\n"
        f"{await t(message.from_user.id, 'invite_forward')}"
    )
    questions = await get_questions(message.from_user.id)
    await state.set_state(IntakeStates.waiting_answers)
    await state.update_data(case_id=case_id, role="A", question_index=0, share_mode="summary")
    await message.answer(invite_text)
    await message.answer(
        f"{await t(message.from_user.id, 'your_side_intro')}\n\n"
        f"{await format_case_header(message.from_user.id, title)}\n\n"
        f"{questions[0][1]}\n\n{await t(message.from_user.id, 'share_prompt')}",
        reply_markup=await share_mode_keyboard(message.from_user.id),
    )


@dp.message(Command("join"))
async def join_case(message: Message, command: CommandObject, state: FSMContext):
    if not command.args:
        await message.answer(await t(message.from_user.id, "join_usage"))
        return
    join_code = command.args.strip()
    async with await get_db() as db:
        cursor = await db.execute("SELECT id, participant_a_user_id, participant_b_user_id, title, conflict_period FROM cases WHERE join_code = ?", (join_code,))
        row = await cursor.fetchone()
        if not row:
            await message.answer(await t(message.from_user.id, "case_not_found"))
            return
        case_id, participant_a_user_id, participant_b_user_id, title, conflict_period = row
        if participant_b_user_id and participant_b_user_id != message.from_user.id:
            await message.answer(await t(message.from_user.id, "case_already_joined"))
            return
        await db.execute("UPDATE cases SET participant_b_user_id = ?, status = ?, updated_at = ? WHERE id = ?", (message.from_user.id, "intake", await now_iso(), case_id))
        await db.commit()
    questions = await get_questions(message.from_user.id)
    await state.set_state(IntakeStates.waiting_answers)
    await state.update_data(case_id=case_id, role="B", question_index=0, share_mode="summary")
    await message.answer(
        f"{await t(message.from_user.id, 'joined_intro')}\n\n"
        f"{await format_case_header(message.from_user.id, title, conflict_period)}\n\n"
        f"{await t(message.from_user.id, 'start_questions')}\n\n"
        f"{questions[0][1]}\n\n{await t(message.from_user.id, 'share_prompt')}",
        reply_markup=await share_mode_keyboard(message.from_user.id),
    )
    try:
        a_lang = await get_lang(participant_a_user_id)
        a_questions = TEXTS[a_lang]["questions"]
        await bot.send_message(
            participant_a_user_id,
            f"{TEXTS[a_lang]['participant_joined']}\n\n"
            f"{await format_case_header(participant_a_user_id, title, conflict_period)}\n\n"
            f"{TEXTS[a_lang]['answer_questions']}\n\n"
            f"{a_questions[0][1]}",
        )
    except Exception:
        logger.exception("Failed to notify participant A")


@dp.message(Command("feedback"))
async def feedback(message: Message, state: FSMContext):
    await state.set_state(IntakeStates.waiting_feedback)
    await message.answer(await t(message.from_user.id, "feedback_choose"), reply_markup=await feedback_keyboard(message.from_user.id))
    await message.answer(await t(message.from_user.id, "feedback_prompt"))


@dp.message(Command("case"))
async def case_view(message: Message, command: CommandObject):
    if not command.args:
        await message.answer(await t(message.from_user.id, "case_usage"))
        return
    join_code = command.args.strip()
    async with await get_db() as db:
        cursor = await db.execute(
            "SELECT title, conflict_period, status, summary_a, summary_b, common_ground, differences, options_text FROM cases WHERE join_code = ? AND (participant_a_user_id = ? OR participant_b_user_id = ?)",
            (join_code, message.from_user.id, message.from_user.id),
        )
        row = await cursor.fetchone()
    if not row:
        await message.answer(await t(message.from_user.id, "case_access_denied"))
        return
    title, conflict_period, status, summary_a, summary_b, common_ground, differences, options_text = row
    texts = TEXTS[await get_lang(message.from_user.id)]
    body = [await format_case_header(message.from_user.id, title, conflict_period), f"{texts['status']}: {status}"]
    if summary_a:
        body.append(f"\n{texts['side_a']}:\n{summary_a}")
    if summary_b:
        body.append(f"\n{texts['side_b']}:\n{summary_b}")
    if common_ground:
        body.append(f"\n{texts['common_ground']}:\n{common_ground}")
    if differences:
        body.append(f"\n{texts['differences']}:\n{differences}")
    if options_text:
        body.append(f"\n{texts['options']}:\n{options_text}")
    await message.answer("\n".join(body))


@dp.message(Command("mycases"))
async def my_cases(message: Message):
    async with await get_db() as db:
        cursor = await db.execute("SELECT title, conflict_period, join_code, status FROM cases WHERE participant_a_user_id = ? OR participant_b_user_id = ? ORDER BY created_at DESC LIMIT 10", (message.from_user.id, message.from_user.id))
        rows = await cursor.fetchall()
    if not rows:
        await message.answer(await t(message.from_user.id, "no_cases"))
        return
    lines = []
    for title, conflict_period, code, status in rows:
        period = f" ({conflict_period})" if conflict_period else ""
        lines.append(f"• {title}{period} — {status} — code `{code}`")
    await message.answer((await t(message.from_user.id, "my_cases")) + "\n" + "\n".join(lines), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("share:"))
async def share_mode_selected(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.split(":", 1)[1]
    await state.update_data(share_mode=mode)
    texts = TEXTS[await get_lang(callback.from_user.id)]
    mapping = {
        "summary": texts["share_mode_summary"],
        "private": texts["share_mode_private"],
        "quote": texts["share_mode_quote"],
    }
    await callback.answer("OK")
    await callback.message.answer(f"{texts['share_mode_selected']} {mapping.get(mode, mode)}")


@dp.callback_query(F.data.startswith("feedback_area:"))
async def feedback_area_selected(callback: CallbackQuery, state: FSMContext):
    area = callback.data.split(":", 1)[1]
    await state.update_data(feedback_area=area)
    texts = TEXTS[await get_lang(callback.from_user.id)]
    await callback.answer("OK")
    await callback.message.answer(f"{texts['feedback_area_selected']} {texts['feedback_areas'].get(area, area)}. {texts['write_comment']}")


@dp.message(F.text, IntakeStates.waiting_feedback)
async def handle_feedback(message: Message, state: FSMContext):
    data = await state.get_data()
    area = data.get("feedback_area", "other")
    async with await get_db() as db:
        await db.execute("INSERT INTO feedback (case_id, user_id, area, feedback_text, created_at) VALUES (?, ?, ?, ?, ?)", (None, message.from_user.id, area, message.text.strip(), await now_iso()))
        await db.commit()
    await state.clear()
    await message.answer(await t(message.from_user.id, "feedback_saved"))


@dp.message(F.text, IntakeStates.waiting_answers)
async def handle_intake_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    case_id = data["case_id"]
    role = data["role"]
    idx = data.get("question_index", 0)
    share_mode = data.get("share_mode", "summary")
    questions = await get_questions(user_id)
    question_key, _ = questions[idx]
    stored_text = message.text.strip() if share_mode != "private" else f"[PRIVATE] {message.text.strip()}"
    async with await get_db() as db:
        await db.execute(
            "INSERT INTO intake_answers (case_id, user_id, role, question_key, answer_text, created_at, share_mode) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (case_id, user_id, role, question_key, stored_text, await now_iso(), share_mode),
        )
        if question_key == "conflict_date":
            await db.execute("UPDATE cases SET conflict_period = COALESCE(conflict_period, ?), updated_at = ?, status = ? WHERE id = ?", (message.text.strip(), await now_iso(), f"intake_{role.lower()}", case_id))
        else:
            await db.execute("UPDATE cases SET updated_at = ?, status = ? WHERE id = ?", (await now_iso(), f"intake_{role.lower()}", case_id))
        await db.commit()
    idx += 1
    if idx < len(questions):
        await state.update_data(question_index=idx, share_mode="summary")
        await message.answer(await t(user_id, "thinking_next"))
        await message.answer(
            f"{questions[idx][1]}\n\n{await t(user_id, 'share_prompt')}",
            reply_markup=await share_mode_keyboard(user_id),
        )
        return
    await state.clear()
    await message.answer(await t(user_id, "position_saved"))
    await message.answer(await t(user_id, "thinking_analysis"))
    await maybe_finalize_case(case_id)


async def maybe_finalize_case(case_id: str):
    async with await get_db() as db:
        cursor = await db.execute("SELECT role, question_key, answer_text FROM intake_answers WHERE case_id = ? ORDER BY id ASC", (case_id,))
        answers = await cursor.fetchall()
        cursor = await db.execute("SELECT participant_a_user_id, participant_b_user_id, title, conflict_period FROM cases WHERE id = ?", (case_id,))
        case_row = await cursor.fetchone()
    if not case_row:
        return
    a_id, b_id, title, conflict_period = case_row
    grouped = {"A": [], "B": []}
    for role, qkey, text in answers:
        grouped[role].append(f"{qkey}: {text}")
    lang_a = await get_lang(a_id)
    lang_b = await get_lang(b_id)
    if len(grouped["A"]) < len(TEXTS[lang_a]["questions"]) or len(grouped["B"]) < len(TEXTS[lang_b]["questions"]):
        return
    async with await get_db() as db:
        await db.execute("UPDATE cases SET status = ?, updated_at = ? WHERE id = ?", ("analyzing", await now_iso(), case_id))
        await db.commit()
    analysis_ru = await analyze_positions("\n".join(grouped["A"]), "\n".join(grouped["B"]), language="ru")
    analysis_en = await analyze_positions("\n".join(grouped["A"]), "\n".join(grouped["B"]), language="en")
    options_text_ru = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(analysis_ru["options"])])
    async with await get_db() as db:
        await db.execute(
            "UPDATE cases SET status = ?, updated_at = ?, summary_a = ?, summary_b = ?, common_ground = ?, differences = ?, options_text = ? WHERE id = ?",
            ("analysis_ready", await now_iso(), analysis_ru["summary_a"], analysis_ru["summary_b"], analysis_ru["common_ground"], analysis_ru["differences"], options_text_ru, case_id),
        )
        await db.commit()
    for uid in [a_id, b_id]:
        if not uid:
            continue
        user_lang = await get_lang(uid)
        texts = TEXTS[user_lang]
        analysis = analysis_ru if user_lang == "ru" else analysis_en
        options_text = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(analysis["options"])])
        report = (
            f"{await format_case_header(uid, title, conflict_period)}\n\n"
            f"{texts['side_a']}:\n{analysis['summary_a']}\n\n"
            f"{texts['side_b']}:\n{analysis['summary_b']}\n\n"
            f"{texts['common_ground']}:\n{analysis['common_ground']}\n\n"
            f"{texts['differences']}:\n{analysis['differences']}\n\n"
            f"{texts['options']}:\n{options_text}"
        )
        try:
            await bot.send_message(uid, report)
            await bot.send_message(uid, texts["feedback_nudge"])
        except Exception:
            logger.exception("Failed to send report")


@dp.message(F.text.startswith("/"))
async def unknown_command(message: Message):
    await message.answer(await t(message.from_user.id, "unknown_command"))


@dp.message(F.text)
async def outside_dialog_text(message: Message):
    await message.answer(await t(message.from_user.id, "outside_text"), reply_markup=main_menu_keyboard(TEXTS[await get_lang(message.from_user.id)]))


async def on_startup():
    await init_db(settings.database_path)


async def main():
    await on_startup()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
