import asyncio
import logging
import secrets
import uuid
from datetime import datetime, UTC

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.deep_linking import create_start_link, decode_payload

from app.config import settings
from app.db import init_db
from app.llm import analyze_positions
from app.texts import TEXTS


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


async def t(key: str):
    return TEXTS[key]


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=TEXTS["menu_create"], callback_data="menu:newcase")],
        [InlineKeyboardButton(text=TEXTS["menu_list"], callback_data="menu:mycases")],
        [InlineKeyboardButton(text=TEXTS["menu_feedback"], callback_data="menu:feedback")],
    ])


def format_case_header(title: str, conflict_period: str | None = None) -> str:
    header = f"{TEXTS['topic']}: {title}"
    if conflict_period:
        header += f"\n{TEXTS['period']}: {conflict_period}"
    return header


def human_status(status: str) -> str:
    return TEXTS.get(f"status_{status}", status)


def next_step_hint(status: str) -> str:
    return TEXTS.get(f"next_{status}", status)


def discussion_actions_keyboard(join_code: str, status: str | None = None) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(text=TEXTS["open_discussion"], callback_data=f"discussion:open:{join_code}"),
        InlineKeyboardButton(text=TEXTS["discussion_invite"], callback_data=f"discussion:invite:{join_code}"),
    ]]
    if status in {"waiting_for_b", "intake", "intake_a", "intake_b", "continues", "analysis_ready"}:
        rows.append([InlineKeyboardButton(text=TEXTS["discussion_continue"], callback_data=f"discussion:continue:{join_code}")])
    if status in {"analysis_ready", "resolved", "paused", "continues"}:
        rows.append([InlineKeyboardButton(text=TEXTS["discussion_feedback"], callback_data=f"discussion:feedback:{join_code}")])
    rows.append([InlineKeyboardButton(text=TEXTS["discussion_delete"], callback_data=f"discussion:delete:{join_code}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def delete_confirm_keyboard(join_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=TEXTS["delete_yes"], callback_data=f"discussion:delete_confirm:{join_code}")],
        [InlineKeyboardButton(text=TEXTS["delete_no"], callback_data=f"discussion:delete_cancel:{join_code}")],
    ])


def decision_keyboard(case_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=TEXTS["decision_resolve"], callback_data=f"decision:resolved:{case_id}")],
        [InlineKeyboardButton(text=TEXTS["decision_continue"], callback_data=f"decision:continues:{case_id}")],
        [InlineKeyboardButton(text=TEXTS["decision_pause"], callback_data=f"decision:paused:{case_id}")],
    ])


async def resolve_case_decision(case_id: str):
    async with await get_db() as db:
        cursor = await db.execute("SELECT participant_a_user_id, participant_b_user_id FROM cases WHERE id = ?", (case_id,))
        row = await cursor.fetchone()
        cursor = await db.execute("SELECT user_id, action FROM case_actions WHERE case_id = ?", (case_id,))
        votes = dict(await cursor.fetchall())
    if not row:
        return None
    a_id, b_id = row
    if a_id not in votes or b_id not in votes:
        return {"ready": False, "a_id": a_id, "b_id": b_id}
    a_action = votes[a_id]
    b_action = votes[b_id]
    if a_action == b_action == "resolved":
        return {"ready": True, "status": "resolved", "text_key": "decision_both_resolved", "a_id": a_id, "b_id": b_id}
    if a_action == b_action == "continues":
        return {"ready": True, "status": "continues", "text_key": "decision_both_continue", "a_id": a_id, "b_id": b_id}
    if a_action == b_action == "paused":
        return {"ready": True, "status": "paused", "text_key": "decision_both_pause", "a_id": a_id, "b_id": b_id}
    return {"ready": True, "status": "continues", "text_key": "decision_mixed", "a_id": a_id, "b_id": b_id}


@dp.callback_query(F.data.startswith("menu:"))
async def main_menu_action(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":", 1)[1]
    await callback.answer("OK")
    if action == "newcase":
        await new_case(callback.message, state)
    elif action == "mycases":
        await my_cases(callback.message, user_id=callback.from_user.id)
    elif action == "feedback":
        await feedback(callback.message, state)


@dp.callback_query(F.data.startswith("discussion:"))
async def discussion_action(callback: CallbackQuery, state: FSMContext):
    _, action, join_code = callback.data.split(":", 2)
    user_id = callback.from_user.id
    await callback.answer("OK")
    if action == "open":
        fake_command = CommandObject(prefix="/", command="case", args=join_code)
        await case_view(callback.message, fake_command, user_id=user_id)
        return
    if action == "invite":
        async with await get_db() as db:
            cursor = await db.execute(
                "SELECT title, conflict_period FROM cases WHERE join_code = ? AND (participant_a_user_id = ? OR participant_b_user_id = ?)",
                (join_code, user_id, user_id),
            )
            row = await cursor.fetchone()
        if not row:
            await callback.message.answer(await t("case_access_denied"))
            return
        title, conflict_period = row
        invite_link = await create_start_link(bot, f"join_{join_code}", encode=True)
        invite_text = (
            f"Вас пригласили в обсуждение конфликта.\n\n"
            f"{format_case_header(title, conflict_period)}\n\n"
            f"Бот поможет спокойно разобрать ситуацию и по очереди задаст вопросы обеим сторонам.\n\n"
            f"Чтобы присоединиться, откройте ссылку:\n{invite_link}"
        )
        await callback.message.answer(invite_text)
        return
    if action == "continue":
        async with await get_db() as db:
            cursor = await db.execute(
                "SELECT id, status, participant_a_user_id, participant_b_user_id, title, conflict_period FROM cases WHERE join_code = ? AND (participant_a_user_id = ? OR participant_b_user_id = ?)",
                (join_code, user_id, user_id),
            )
            row = await cursor.fetchone()
        if not row:
            await callback.message.answer(await t("case_access_denied"))
            return
        case_id, status, a_id, b_id, title, conflict_period = row
        if status in {"analysis_ready", "continues"}:
            await callback.message.answer(await t("decision_prompt"), reply_markup=decision_keyboard(case_id))
            return
        if status in {"waiting_for_b", "intake_b"} and user_id == a_id:
            await callback.message.answer(next_step_hint(status))
            return
        if status in {"intake", "intake_a"} and user_id == a_id:
            questions = TEXTS["questions"]
            async with await get_db() as db:
                cursor = await db.execute("SELECT COUNT(*) FROM intake_answers WHERE case_id = ? AND user_id = ?", (case_id, user_id))
                answered = (await cursor.fetchone())[0]
            if answered < len(questions):
                await state.set_state(IntakeStates.waiting_answers)
                await state.update_data(case_id=case_id, role="A", question_index=answered, share_mode="summary")
                await callback.message.answer(f"{format_case_header(title, conflict_period)}\n\n{questions[answered][1]}")
                return
        if status in {"intake", "intake_b"} and user_id == b_id:
            questions = TEXTS["questions"]
            async with await get_db() as db:
                cursor = await db.execute("SELECT COUNT(*) FROM intake_answers WHERE case_id = ? AND user_id = ?", (case_id, user_id))
                answered = (await cursor.fetchone())[0]
            if answered < len(questions):
                await state.set_state(IntakeStates.waiting_answers)
                await state.update_data(case_id=case_id, role="B", question_index=answered, share_mode="summary")
                await callback.message.answer(f"{format_case_header(title, conflict_period)}\n\n{questions[answered][1]}")
                return
        await callback.message.answer(next_step_hint(status))
        return
    if action == "feedback":
        await state.set_state(IntakeStates.waiting_feedback)
        await state.update_data(feedback_case_code=join_code, feedback_area="other")
        await callback.message.answer(await t("feedback_prompt"))
        return
    if action == "delete":
        async with await get_db() as db:
            cursor = await db.execute("SELECT id FROM cases WHERE join_code = ? AND creator_user_id = ?", (join_code, user_id))
            row = await cursor.fetchone()
        if not row:
            await callback.message.answer(await t("case_access_denied"))
            return
        await callback.message.answer(await t("delete_confirm"), reply_markup=delete_confirm_keyboard(join_code))
        return
    if action == "delete_confirm":
        async with await get_db() as db:
            cursor = await db.execute("SELECT id FROM cases WHERE join_code = ? AND creator_user_id = ?", (join_code, user_id))
            row = await cursor.fetchone()
            if not row:
                await callback.message.answer(await t("case_access_denied"))
                return
            case_id = row[0]
            await db.execute("DELETE FROM case_actions WHERE case_id = ?", (case_id,))
            await db.execute("DELETE FROM intake_answers WHERE case_id = ?", (case_id,))
            await db.execute("DELETE FROM feedback WHERE case_id = ?", (case_id,))
            await db.execute("DELETE FROM cases WHERE id = ?", (case_id,))
            await db.commit()
        await callback.message.answer(await t("discussion_deleted"))
        return
    if action == "delete_cancel":
        await callback.message.answer(await t("outside_text"))


@dp.callback_query(F.data.startswith("decision:"))
async def decision_action(callback: CallbackQuery):
    _, action, case_id = callback.data.split(":", 2)
    user_id = callback.from_user.id
    async with await get_db() as db:
        await db.execute(
            "INSERT INTO case_actions (case_id, user_id, action, created_at) VALUES (?, ?, ?, ?) ON CONFLICT(case_id, user_id) DO UPDATE SET action=excluded.action, created_at=excluded.created_at",
            (case_id, user_id, action, await now_iso()),
        )
        await db.commit()
    await callback.answer("OK")
    await callback.message.answer(await t("decision_saved"))
    result = await resolve_case_decision(case_id)
    if not result:
        return
    if not result["ready"]:
        await callback.message.answer(await t("decision_wait_other"))
        return
    async with await get_db() as db:
        await db.execute("UPDATE cases SET status = ?, updated_at = ? WHERE id = ?", (result["status"], await now_iso(), case_id))
        await db.commit()
    for uid in [result["a_id"], result["b_id"]]:
        if uid:
            try:
                await bot.send_message(uid, await t(result["text_key"]))
            except Exception:
                logger.exception("Failed to send decision result")


@dp.message(StateFilter('*'), Command("start"))
async def start(message: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    if command.args:
        payload = decode_payload(command.args)
        if payload.startswith("join_"):
            join_code = payload.removeprefix("join_")
            fake_command = CommandObject(prefix="/", command="join", args=join_code)
            await join_case(message, fake_command, state)
            return
    await message.answer(await t("intro"), reply_markup=main_menu_keyboard())


@dp.message(StateFilter('*'), Command("newcase"))
async def new_case(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(IntakeStates.waiting_case_title)
    await message.answer(await t("newcase_prompt"))


@dp.message(IntakeStates.waiting_case_title)
async def receive_case_title(message: Message, state: FSMContext):
    case_id = str(uuid.uuid4())
    join_code = secrets.token_hex(3)
    title = message.text.strip()
    created_at = await now_iso()
    async with await get_db() as db:
        await db.execute("INSERT INTO cases (id, creator_user_id, participant_a_user_id, title, join_code, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (case_id, message.from_user.id, message.from_user.id, title, join_code, "waiting_for_b", created_at, created_at))
        await db.commit()
    invite_link = await create_start_link(bot, f"join_{join_code}", encode=True)
    invite_text = (
        f"Вас пригласили в обсуждение конфликта.\n\n"
        f"{format_case_header(title)}\n\n"
        f"Бот поможет спокойно разобрать ситуацию и по очереди задаст вопросы обеим сторонам.\n\n"
        f"Чтобы присоединиться, откройте ссылку:\n{invite_link}"
    )
    questions = TEXTS["questions"]
    await state.set_state(IntakeStates.waiting_answers)
    await state.update_data(case_id=case_id, role="A", question_index=0, share_mode="summary")
    await message.answer(invite_text)
    await message.answer(f"{await t('your_side_intro')}\n\n{format_case_header(title)}\n\n{questions[0][1]}")


@dp.message(StateFilter('*'), Command("join"))
async def join_case(message: Message, command: CommandObject, state: FSMContext):
    if not command.args:
        await message.answer(await t("join_usage"))
        return
    join_code = command.args.strip()
    async with await get_db() as db:
        cursor = await db.execute("SELECT id, participant_a_user_id, participant_b_user_id, title, conflict_period FROM cases WHERE join_code = ?", (join_code,))
        row = await cursor.fetchone()
        if not row:
            await message.answer(await t("case_not_found"))
            return
        case_id, participant_a_user_id, participant_b_user_id, title, conflict_period = row
        if participant_b_user_id and participant_b_user_id != message.from_user.id:
            await message.answer(await t("case_already_joined"))
            return
        await db.execute("UPDATE cases SET participant_b_user_id = ?, status = ?, updated_at = ? WHERE id = ?", (message.from_user.id, "intake", await now_iso(), case_id))
        await db.commit()
    questions = TEXTS["questions"]
    await state.set_state(IntakeStates.waiting_answers)
    await state.update_data(case_id=case_id, role="B", question_index=0, share_mode="summary")
    await message.answer(f"{await t('joined_intro')}\n\n{format_case_header(title, conflict_period)}\n\n{await t('start_questions')}\n\n{questions[0][1]}")
    try:
        await bot.send_message(participant_a_user_id, f"{await t('participant_joined')}\n\n{format_case_header(title, conflict_period)}\n\n{await t('answer_questions')}\n\n{questions[0][1]}")
    except Exception:
        logger.exception("Failed to notify participant A")


@dp.message(StateFilter('*'), Command("feedback"))
async def feedback(message: Message, state: FSMContext):
    await state.set_state(IntakeStates.waiting_feedback)
    await state.update_data(feedback_area="other")
    await message.answer(await t("feedback_prompt"))


@dp.message(StateFilter('*'), Command("case"))
async def case_view(message: Message, command: CommandObject, user_id: int | None = None):
    user_id = user_id or message.from_user.id
    if not command.args:
        await message.answer(await t("case_usage"))
        return
    join_code = command.args.strip()
    async with await get_db() as db:
        cursor = await db.execute("SELECT title, conflict_period, status, summary_a, summary_b, common_ground, differences, options_text FROM cases WHERE join_code = ? AND (participant_a_user_id = ? OR participant_b_user_id = ?)", (join_code, user_id, user_id))
        row = await cursor.fetchone()
    if not row:
        await message.answer(await t("case_access_denied"))
        return
    title, conflict_period, status, summary_a, summary_b, common_ground, differences, options_text = row
    body = [format_case_header(title, conflict_period), f"{await t('status')}: {human_status(status)}", f"{await t('next_step')}: {next_step_hint(status)}"]
    if summary_a:
        body.append(f"\n{await t('side_a')}:\n{summary_a}")
        body.append(f"\n{await t('side_b')}:\n{summary_b}")
        body.append(f"\n{await t('common_ground')}:\n{common_ground}")
        body.append(f"\n{await t('differences')}:\n{differences}")
        body.append(f"\n{await t('options')}:\n{options_text}")
    await message.answer("\n".join(body), reply_markup=discussion_actions_keyboard(join_code, status))


@dp.message(StateFilter('*'), Command("mycases"))
async def my_cases(message: Message, user_id: int | None = None):
    user_id = user_id or message.from_user.id
    async with await get_db() as db:
        cursor = await db.execute("SELECT title, conflict_period, join_code, status FROM cases WHERE participant_a_user_id = ? OR participant_b_user_id = ? ORDER BY created_at DESC LIMIT 10", (user_id, user_id))
        rows = await cursor.fetchall()
    if not rows:
        await message.answer(await t("no_cases"))
        return
    await message.answer(await t("my_cases"))
    for title, conflict_period, code, status in rows:
        text = f"{format_case_header(title, conflict_period)}\n{await t('status')}: {human_status(status)}\n{await t('code_label')}: `{code}`"
        await message.answer(text, parse_mode="Markdown", reply_markup=discussion_actions_keyboard(code, status))


@dp.message(F.text, IntakeStates.waiting_feedback)
async def handle_feedback(message: Message, state: FSMContext):
    data = await state.get_data()
    area = data.get("feedback_area", "other")
    join_code = data.get("feedback_case_code")
    feedback_text = message.text.strip()
    case_id = None
    case_title = None
    if join_code:
        async with await get_db() as db:
            cursor = await db.execute("SELECT id, title FROM cases WHERE join_code = ?", (join_code,))
            row = await cursor.fetchone()
            if row:
                case_id, case_title = row
    async with await get_db() as db:
        await db.execute("INSERT INTO feedback (case_id, user_id, area, feedback_text, created_at) VALUES (?, ?, ?, ?, ?)", (case_id, message.from_user.id, area, feedback_text, await now_iso()))
        await db.commit()
    await state.clear()
    await message.answer(await t("feedback_saved"))
    if settings.owner_telegram_id:
        try:
            area_label = TEXTS["feedback_areas"].get(area, area)
            owner_text = (
                "Новый отзыв о боте\n\n"
                f"От пользователя: {message.from_user.id}\n"
                f"Область: {area_label}\n"
                + (f"Обсуждение: {case_title} ({join_code})\n" if case_title and join_code else "")
                + f"Текст: {feedback_text}"
            )
            await bot.send_message(settings.owner_telegram_id, owner_text)
        except Exception:
            logger.exception("Failed to forward feedback to owner")


@dp.message(F.text, IntakeStates.waiting_answers)
async def handle_intake_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    case_id = data["case_id"]
    role = data["role"]
    idx = data.get("question_index", 0)
    questions = TEXTS["questions"]
    question_key, _ = questions[idx]
    async with await get_db() as db:
        await db.execute("INSERT INTO intake_answers (case_id, user_id, role, question_key, answer_text, created_at, share_mode) VALUES (?, ?, ?, ?, ?, ?, ?)", (case_id, user_id, role, question_key, message.text.strip(), await now_iso(), 'summary'))
        if question_key == "conflict_date":
            await db.execute("UPDATE cases SET conflict_period = COALESCE(conflict_period, ?), updated_at = ?, status = ? WHERE id = ?", (message.text.strip(), await now_iso(), f"intake_{role.lower()}", case_id))
        else:
            await db.execute("UPDATE cases SET updated_at = ?, status = ? WHERE id = ?", (await now_iso(), f"intake_{role.lower()}", case_id))
        await db.commit()
    idx += 1
    if idx < len(questions):
        await state.update_data(question_index=idx, share_mode="summary")
        await message.answer(await t("thinking_next"))
        await message.answer(questions[idx][1])
        return
    await state.clear()
    await message.answer(await t("position_saved"))
    await message.answer(await t("thinking_analysis"))
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
    if len(grouped["A"]) < len(TEXTS["questions"]) or len(grouped["B"]) < len(TEXTS["questions"]):
        return
    async with await get_db() as db:
        await db.execute("UPDATE cases SET status = ?, updated_at = ? WHERE id = ?", ("analyzing", await now_iso(), case_id))
        await db.commit()
    try:
        analysis_ru = await asyncio.wait_for(analyze_positions("\n".join(grouped["A"]), "\n".join(grouped["B"]), language="ru"), timeout=60)
    except Exception:
        logger.exception("Failed to analyze case %s", case_id)
        async with await get_db() as db:
            await db.execute("UPDATE cases SET status = ?, updated_at = ? WHERE id = ?", ("continues", await now_iso(), case_id))
            await db.commit()
        for uid in [a_id, b_id]:
            if uid:
                try:
                    await bot.send_message(uid, await t("analysis_failed"))
                except Exception:
                    logger.exception("Failed to send analysis failure notice")
        return
    options_text_ru = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(analysis_ru["options"])])
    async with await get_db() as db:
        await db.execute("UPDATE cases SET status = ?, updated_at = ?, summary_a = ?, summary_b = ?, common_ground = ?, differences = ?, options_text = ? WHERE id = ?", ("analysis_ready", await now_iso(), analysis_ru["summary_a"], analysis_ru["summary_b"], analysis_ru["common_ground"], analysis_ru["differences"], options_text_ru, case_id))
        await db.commit()
    report = (
        f"{format_case_header(title, conflict_period)}\n\n"
        f"{await t('side_a')}:\n{analysis_ru['summary_a']}\n\n"
        f"{await t('side_b')}:\n{analysis_ru['summary_b']}\n\n"
        f"{await t('common_ground')}:\n{analysis_ru['common_ground']}\n\n"
        f"{await t('differences')}:\n{analysis_ru['differences']}\n\n"
        f"{await t('options')}:\n{options_text_ru}"
    )
    for uid in [a_id, b_id]:
        if uid:
            try:
                await bot.send_message(uid, report)
                await bot.send_message(uid, await t("decision_prompt"), reply_markup=decision_keyboard(case_id))
                await bot.send_message(uid, await t("feedback_nudge"))
            except Exception:
                logger.exception("Failed to send report")


@dp.message(F.text.startswith("/"))
async def unknown_command(message: Message):
    await message.answer(await t("unknown_command"))


@dp.message(F.text)
async def outside_dialog_text(message: Message):
    await message.answer(await t("outside_text"), reply_markup=main_menu_keyboard())


async def setup_bot_commands():
    ru_commands = [
        BotCommand(command="start", description="Открыть главное меню"),
        BotCommand(command="newcase", description="Создать новое обсуждение"),
        BotCommand(command="mycases", description="Мои обсуждения"),
        BotCommand(command="feedback", description="Оставить отзыв"),
    ]
    await bot.set_my_commands(ru_commands, scope=BotCommandScopeAllPrivateChats())


async def on_startup():
    await init_db(settings.database_path)
    await setup_bot_commands()


async def main():
    await on_startup()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
