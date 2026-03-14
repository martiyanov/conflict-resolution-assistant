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
from app.texts import FEEDBACK_PROMPT, INTRO, QUESTIONS, SHARE_MODE_TEXT, THINKING_ANALYSIS, THINKING_NEXT_QUESTION, UNKNOWN_COMMAND


logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

bot = Bot(settings.bot_token)
dp = Dispatcher()


class IntakeStates(StatesGroup):
    waiting_case_title = State()
    waiting_answers = State()
    waiting_feedback = State()


def format_case_header(title: str, conflict_period: str | None = None) -> str:
    header = f"Тема конфликта: {title}"
    if conflict_period:
        header += f"\nКогда/период: {conflict_period}"
    return header


async def now_iso():
    return datetime.now(UTC).isoformat()


async def get_db():
    return aiosqlite.connect(settings.database_path)


def share_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Только summary", callback_data="share:summary")],
        [InlineKeyboardButton(text="Полностью приватно", callback_data="share:private")],
        [InlineKeyboardButton(text="Можно цитировать", callback_data="share:quote")],
    ])


def feedback_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Корректность вопросов", callback_data="feedback_area:questions")],
        [InlineKeyboardButton(text="Flow бота", callback_data="feedback_area:flow")],
        [InlineKeyboardButton(text="Качество summary", callback_data="feedback_area:summary")],
        [InlineKeyboardButton(text="UX / интерфейс", callback_data="feedback_area:ux")],
        [InlineKeyboardButton(text="Другое", callback_data="feedback_area:other")],
    ])


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
    await message.answer(INTRO)


@dp.message(Command("newcase"))
async def new_case(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(IntakeStates.waiting_case_title)
    await message.answer("Опиши конфликт одним предложением. Это будет название кейса.")


@dp.message(IntakeStates.waiting_case_title)
async def receive_case_title(message: Message, state: FSMContext):
    case_id = str(uuid.uuid4())
    join_code = secrets.token_hex(3)
    title = message.text.strip()
    created_at = await now_iso()
    async with await get_db() as db:
        await db.execute(
            """
            INSERT INTO cases (id, creator_user_id, participant_a_user_id, title, join_code, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (case_id, message.from_user.id, message.from_user.id, title, join_code, "waiting_for_b", created_at, created_at),
        )
        await db.commit()
    invite_link = await create_start_link(bot, f"join_{join_code}", encode=True)
    invite_text = (
        f"Приглашение в кейс по конфликту:\n\n"
        f"{format_case_header(title)}\n\n"
        f"Ссылка для второго участника:\n{invite_link}\n\n"
        f"Можно просто переслать это сообщение собеседнику."
    )
    await state.set_state(IntakeStates.waiting_answers)
    await state.update_data(case_id=case_id, role="A", question_index=0, share_mode="summary")
    await message.answer(invite_text)
    await message.answer(
        "А пока можешь сразу ответить на вопросы по своей стороне конфликта.\n\n"
        + format_case_header(title)
        + "\n\n"
        + QUESTIONS[0][1]
        + "\n\nКак можно использовать этот ответ при пересказе второй стороне?",
        reply_markup=share_mode_keyboard(),
    )


@dp.message(Command("join"))
async def join_case(message: Message, command: CommandObject, state: FSMContext):
    if not command.args:
        await message.answer("Использование: /join CODE")
        return
    join_code = command.args.strip()
    async with await get_db() as db:
        cursor = await db.execute("SELECT id, participant_a_user_id, participant_b_user_id, status, title, conflict_period FROM cases WHERE join_code = ?", (join_code,))
        row = await cursor.fetchone()
        if not row:
            await message.answer("Кейс с таким кодом не найден.")
            return
        case_id, participant_a_user_id, participant_b_user_id, status, title, conflict_period = row
        if participant_b_user_id and participant_b_user_id != message.from_user.id:
            await message.answer("К этому кейсу уже присоединился второй участник.")
            return
        await db.execute(
            "UPDATE cases SET participant_b_user_id = ?, status = ?, updated_at = ? WHERE id = ?",
            (message.from_user.id, "intake", await now_iso(), case_id),
        )
        await db.commit()
    await state.set_state(IntakeStates.waiting_answers)
    await state.update_data(case_id=case_id, role="B", question_index=0, share_mode="summary")
    await message.answer(
        "Ты присоединился к кейсу. Ниже тема, по которой будут вопросы.\n\n"
        + format_case_header(title, conflict_period)
        + "\n\nНачнём с короткого опроса.\n\n"
        + QUESTIONS[0][1]
        + "\n\nКак можно использовать этот ответ при пересказе второй стороне?",
        reply_markup=share_mode_keyboard(),
    )
    try:
        await bot.send_message(
            participant_a_user_id,
            "Второй участник присоединился. Теперь можно продолжать по теме:\n\n"
            + format_case_header(title, conflict_period)
            + "\n\nОтветь на вопросы.\n\n"
            + QUESTIONS[0][1],
        )
    except Exception:
        logger.exception("Failed to notify participant A")


@dp.message(Command("feedback"))
async def feedback(message: Message, state: FSMContext):
    await state.set_state(IntakeStates.waiting_feedback)
    await message.answer("Выбери область, по которой хочешь оставить обратную связь.", reply_markup=feedback_keyboard())
    await message.answer(FEEDBACK_PROMPT)


@dp.message(Command("case"))
async def case_view(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Использование: /case CODE")
        return
    join_code = command.args.strip()
    async with await get_db() as db:
        cursor = await db.execute(
            "SELECT id, title, conflict_period, status, summary_a, summary_b, common_ground, differences, options_text FROM cases WHERE join_code = ? AND (participant_a_user_id = ? OR participant_b_user_id = ?)",
            (join_code, message.from_user.id, message.from_user.id),
        )
        row = await cursor.fetchone()
    if not row:
        await message.answer("Кейс не найден или у тебя нет к нему доступа.")
        return
    _, title, conflict_period, status, summary_a, summary_b, common_ground, differences, options_text = row
    body = [format_case_header(title, conflict_period), f"Статус: {status}"]
    if summary_a:
        body.append(f"\nПозиция A:\n{summary_a}")
    if summary_b:
        body.append(f"\nПозиция B:\n{summary_b}")
    if common_ground:
        body.append(f"\nОбщее:\n{common_ground}")
    if differences:
        body.append(f"\nРасхождения:\n{differences}")
    if options_text:
        body.append(f"\nВарианты:\n{options_text}")
    await message.answer("\n".join(body))


@dp.message(Command("mycases"))
async def my_cases(message: Message):
    async with await get_db() as db:
        cursor = await db.execute(
            "SELECT title, conflict_period, join_code, status FROM cases WHERE participant_a_user_id = ? OR participant_b_user_id = ? ORDER BY created_at DESC LIMIT 10",
            (message.from_user.id, message.from_user.id),
        )
        rows = await cursor.fetchall()
    if not rows:
        await message.answer("У тебя пока нет кейсов.")
        return
    lines = []
    for title, conflict_period, code, status in rows:
        period = f" ({conflict_period})" if conflict_period else ""
        lines.append(f"• {title}{period} — {status} — code `{code}`")
    await message.answer("Твои кейсы:\n" + "\n".join(lines), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("share:"))
async def share_mode_selected(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.split(":", 1)[1]
    await state.update_data(share_mode=mode)
    await callback.answer("Принято")
    await callback.message.answer(f"Режим приватности для следующего ответа: {SHARE_MODE_TEXT.get(mode, mode)}")


@dp.callback_query(F.data.startswith("feedback_area:"))
async def feedback_area_selected(callback: CallbackQuery, state: FSMContext):
    area = callback.data.split(":", 1)[1]
    await state.update_data(feedback_area=area)
    await callback.answer("Ок")
    await callback.message.answer(f"Область обратной связи: {area}. Теперь напиши короткий комментарий.")


@dp.message(F.text, IntakeStates.waiting_feedback)
async def handle_feedback(message: Message, state: FSMContext):
    data = await state.get_data()
    area = data.get("feedback_area", "other")
    async with await get_db() as db:
        await db.execute(
            "INSERT INTO feedback (case_id, user_id, area, feedback_text, created_at) VALUES (?, ?, ?, ?, ?)",
            (None, message.from_user.id, area, message.text.strip(), await now_iso()),
        )
        await db.commit()
    await state.clear()
    await message.answer("Спасибо. Обратную связь записал.")


@dp.message(F.text, IntakeStates.waiting_answers)
async def handle_intake_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    case_id = data["case_id"]
    role = data["role"]
    idx = data.get("question_index", 0)
    share_mode = data.get("share_mode", "summary")
    question_key, _ = QUESTIONS[idx]
    stored_text = message.text.strip() if share_mode != "private" else f"[PRIVATE] {message.text.strip()}"
    async with await get_db() as db:
        await db.execute(
            "INSERT INTO intake_answers (case_id, user_id, role, question_key, answer_text, created_at, share_mode) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (case_id, message.from_user.id, role, question_key, stored_text, await now_iso(), share_mode),
        )
        if question_key == "conflict_date":
            await db.execute(
                "UPDATE cases SET conflict_period = COALESCE(conflict_period, ?), updated_at = ?, status = ? WHERE id = ?",
                (message.text.strip(), await now_iso(), f"intake_{role.lower()}", case_id),
            )
        else:
            await db.execute("UPDATE cases SET updated_at = ?, status = ? WHERE id = ?", (await now_iso(), f"intake_{role.lower()}", case_id))
        await db.commit()
    idx += 1
    if idx < len(QUESTIONS):
        await state.update_data(question_index=idx, share_mode="summary")
        await message.answer(THINKING_NEXT_QUESTION)
        await message.answer(
            QUESTIONS[idx][1] + "\n\nКак можно использовать этот ответ при пересказе второй стороне?",
            reply_markup=share_mode_keyboard(),
        )
        return
    await state.clear()
    await message.answer("Спасибо. Твоя позиция записана.")
    await message.answer(THINKING_ANALYSIS)
    await maybe_finalize_case(case_id)


async def maybe_finalize_case(case_id: str):
    async with await get_db() as db:
        cursor = await db.execute(
            "SELECT role, question_key, answer_text FROM intake_answers WHERE case_id = ? ORDER BY id ASC",
            (case_id,),
        )
        answers = await cursor.fetchall()
        cursor = await db.execute(
            "SELECT participant_a_user_id, participant_b_user_id, title, conflict_period FROM cases WHERE id = ?",
            (case_id,),
        )
        case_row = await cursor.fetchone()
    if not case_row:
        return
    a_id, b_id, title, conflict_period = case_row
    grouped = {"A": [], "B": []}
    for role, qkey, text in answers:
        grouped[role].append(f"{qkey}: {text}")
    if len(grouped["A"]) < len(QUESTIONS) or len(grouped["B"]) < len(QUESTIONS):
        return
    async with await get_db() as db:
        await db.execute("UPDATE cases SET status = ?, updated_at = ? WHERE id = ?", ("analyzing", await now_iso(), case_id))
        await db.commit()
    analysis = await analyze_positions("\n".join(grouped["A"]), "\n".join(grouped["B"]))
    options_text = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(analysis["options"])])
    async with await get_db() as db:
        await db.execute(
            """
            UPDATE cases
            SET status = ?, updated_at = ?, summary_a = ?, summary_b = ?, common_ground = ?, differences = ?, options_text = ?
            WHERE id = ?
            """,
            (
                "analysis_ready",
                await now_iso(),
                analysis["summary_a"],
                analysis["summary_b"],
                analysis["common_ground"],
                analysis["differences"],
                options_text,
                case_id,
            ),
        )
        await db.commit()
    report = (
        f"{format_case_header(title, conflict_period)}\n\n"
        f"Позиция A:\n{analysis['summary_a']}\n\n"
        f"Позиция B:\n{analysis['summary_b']}\n\n"
        f"Общее:\n{analysis['common_ground']}\n\n"
        f"Расхождения:\n{analysis['differences']}\n\n"
        f"Варианты следующего шага:\n{options_text}"
    )
    for uid in [a_id, b_id]:
        if uid:
            try:
                await bot.send_message(uid, report)
                await bot.send_message(uid, "Если захочешь, можешь оставить короткую обратную связь через /feedback")
            except Exception:
                logger.exception("Failed to send report")


async def on_startup():
    await init_db(settings.database_path)


@dp.message(F.text.startswith("/"))
async def unknown_command(message: Message):
    await message.answer(UNKNOWN_COMMAND)


async def main():
    await on_startup()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
