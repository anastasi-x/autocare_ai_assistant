import re
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.cache import cache
from src.config import settings
from src.customer_parser import CustomerRequest, is_customer_request, parse_customer_request
from src.dialog_memory import dialog_memory
from src.rag import answer_question
from src.sheets import (
    _description_for_sheet,
    cancel_customer_request,
    find_customer_request,
    list_customer_requests_by_telegram,
    save_request_to_sheet,
)


router = Router()
MAX_ACTIVE_REQUESTS_PER_USER = 3
PENDING_CANCEL_SELECTIONS: dict[int, list[dict[str, str]]] = {}
PENDING_CANCEL_CONFIRMATIONS: dict[int, dict[str, str]] = {}
PENDING_CREATE_CONFIRMATIONS: dict[int, CustomerRequest] = {}
PENDING_REQUEST_DRAFTS: dict[int, CustomerRequest] = {}
CONFIRMED_CREATE_USERS: set[int] = set()


START_MESSAGE = """👋 <b>Здравствуйте!</b>

Я <b>AutoCare AI</b> — виртуальный ассистент постпродажного обслуживания.

<b>Помогу вам:</b>

• Узнать информацию по гарантии
• Уточнить вопросы по сервису и ТО
• Сориентироваться по диагностике и ремонту
• Подготовить обращение специалисту
• Передать заявку на запись в сервис
• Проверить статус обращения, если данных достаточно

Напишите ваш вопрос или кратко опишите ситуацию с автомобилем."""

HELP_MESSAGE = """ℹ️ <b>Чем я могу помочь</b>

Вы можете спросить:

• Что входит в техническое обслуживание
• Как оформить гарантийное обращение
• Как записаться на сервис
• Что делать при ошибке, шуме или неисправности
• Как проверить статус обращения

<b>Для передачи специалисту укажите:</b>

• Имя
• Телефон или email
• Марку и модель автомобиля
• Суть обращения

VIN или госномер можно добавить, если удобно. На первом этапе это не обязательно."""


@router.message(Command("start"))
async def start(message: Message) -> None:
    await _answer_html(message, START_MESSAGE)


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await _answer_html(message, HELP_MESSAGE)


@router.message(Command("clear_cache"))
async def clear_cache(message: Message) -> None:
    if not _is_admin(message):
        await _answer_html(
            message,
            "🔒 <b>Команда доступна только администратору.</b>\n\n"
            "Если нужно обновить ответ бота, просто задайте вопрос заново."
        )
        return

    count = cache.clear()
    await _answer_html(message, f"✅ <b>Кэш очищен.</b>\n\nУдалено записей: {count}.")


@router.message(Command("clear_memory"))
async def clear_memory(message: Message) -> None:
    count = dialog_memory.clear(message.from_user.id if message.from_user else None)
    await _answer_html(
        message,
        f"✅ <b>Память диалога очищена.</b>\n\nУдалено сообщений: {count}.",
    )


@router.message(F.text)
async def handle_text(message: Message) -> None:
    text = message.text or ""
    if _has_pending_cancel_selection(message) and text.strip().isdigit():
        await _handle_cancel_selection(message, int(text.strip()))
        return

    if _is_cancel_request(text):
        await _handle_cancel_request(message)
        return

    if _is_request_list_question(text):
        await _handle_request_list(message)
        return

    if _is_work_schedule_question(text):
        answer = _work_schedule_answer(text)
        _remember(message, text, answer)
        await _answer_html(message, answer)
        return

    request = parse_customer_request(text)
    has_request_draft = bool(message.from_user and message.from_user.id in PENDING_REQUEST_DRAFTS)
    if has_request_draft and message.from_user:
        request = _merge_request_draft(PENDING_REQUEST_DRAFTS[message.from_user.id], request)

    if has_request_draft:
        await _handle_customer_request(message, request)
        return

    if _is_explicit_create_request(text):
        await _handle_customer_request(message, request)
        return

    if request.request_type == "status":
        await _handle_status(message, request)
        return

    if request.request_type == "service_booking" and request.preferred_visit_time:
        await _handle_customer_request(message, request)
        return

    if _is_consultation_question(text, request):
        answer = await answer_question(
            text,
            user_id=message.from_user.id if message.from_user else None,
        )
        answer, reply_markup = _prepare_consultation_response(
            _safe_html(answer),
            text,
            request,
            message.from_user.id if message.from_user else None,
        )
        _remember(message, text, answer)
        await message.answer(answer, parse_mode="HTML", reply_markup=reply_markup)
        return

    if is_customer_request(text):
        _apply_dialog_request_intent(message, request)
        await _handle_customer_request(message, request)
        return

    answer = await answer_question(
        text,
        user_id=message.from_user.id if message.from_user else None,
    )
    _remember(message, text, answer)
    await _answer_html(message, _safe_html(answer))


async def _handle_customer_request(message: Message, request: CustomerRequest) -> None:
    if message.from_user:
        active_requests = list_customer_requests_by_telegram(message.from_user.id)
        active_count = len(active_requests)
        has_create_confirmation = message.from_user.id in CONFIRMED_CREATE_USERS
        needs_new_confirmation = _is_explicit_create_request(request.description)

        if active_count >= MAX_ACTIVE_REQUESTS_PER_USER:
            CONFIRMED_CREATE_USERS.discard(message.from_user.id)
            answer = (
                f"У вас уже есть {active_count} активные заявки. "
                f"Создать новую сейчас не получится: лимит — {MAX_ACTIVE_REQUESTS_PER_USER} заявки на пользователя.\n\n"
                "Можно отменить одну из текущих заявок или дождаться, пока специалист обработает обращение."
            )
            _remember(message, request.description, answer)
            await _answer_html(message, answer)
            return

        if active_count > 0 and (not has_create_confirmation or needs_new_confirmation):
            CONFIRMED_CREATE_USERS.discard(message.from_user.id)
            PENDING_CREATE_CONFIRMATIONS[message.from_user.id] = request
            answer = (
                f"У вас уже есть {_plural_requests(active_count)}.\n\n"
                "Вы точно хотите создать еще одно обращение?"
            )
            _remember(message, request.description, answer)
            await message.answer(
                answer,
                parse_mode="HTML",
                reply_markup=_create_confirmation_keyboard(),
            )
            return

    missing = _missing_required_fields(request)
    if missing:
        if message.from_user:
            PENDING_REQUEST_DRAFTS[message.from_user.id] = request
        answer = _missing_fields_answer(request, missing)
        _remember(message, request.description, answer)
        await _answer_html(message, answer)
        return

    if message.from_user:
        CONFIRMED_CREATE_USERS.discard(message.from_user.id)
        PENDING_REQUEST_DRAFTS.pop(message.from_user.id, None)

    await _save_customer_request(message, request)


async def _save_customer_request(message: Message, request: CustomerRequest) -> None:
    result = save_request_to_sheet(
        request,
        telegram_id=message.from_user.id if message.from_user else None,
        username=message.from_user.username if message.from_user and message.from_user.username else "",
    )
    if result["success"]:
        answer = _success_answer(request)
        _remember(message, request.description, answer)
        await _answer_html(message, answer)
    else:
        answer = f"⚠️ <b>Не удалось передать обращение.</b>\n\n{escape(str(result['error']))}"
        _remember(message, request.description, answer)
        await _answer_html(message, answer)


@router.callback_query(F.data.in_({"create_request_yes", "create_request_no"}))
async def handle_create_confirmation(callback: CallbackQuery) -> None:
    if not callback.from_user:
        await callback.answer()
        return
    if not callback.message:
        await callback.answer("Не удалось обновить сообщение.", show_alert=True)
        return

    request = PENDING_CREATE_CONFIRMATIONS.pop(callback.from_user.id, None)
    if not request:
        await callback.answer("Заявка для создания не найдена.", show_alert=True)
        return

    if callback.data == "create_request_no":
        CONFIRMED_CREATE_USERS.discard(callback.from_user.id)
        PENDING_REQUEST_DRAFTS.pop(callback.from_user.id, None)
        await callback.message.edit_text(
            "✅ Действие отменено.\n\nМогу я чем-нибудь еще вам помочь?",
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.answer()
        return

    active_count = len(list_customer_requests_by_telegram(callback.from_user.id))
    if active_count >= MAX_ACTIVE_REQUESTS_PER_USER:
        await callback.message.edit_text(
            f"У вас уже есть {active_count} активные заявки. "
            f"Создать новую сейчас не получится: лимит — {MAX_ACTIVE_REQUESTS_PER_USER} заявки на пользователя.",
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.answer()
        return

    missing = _missing_required_fields(request)
    if missing:
        CONFIRMED_CREATE_USERS.add(callback.from_user.id)
        PENDING_REQUEST_DRAFTS[callback.from_user.id] = request
        await callback.message.edit_text(
            _missing_fields_answer(request, missing),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.answer()
        return

    result = save_request_to_sheet(
        request,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username or "",
    )
    if result["success"]:
        CONFIRMED_CREATE_USERS.discard(callback.from_user.id)
        PENDING_REQUEST_DRAFTS.pop(callback.from_user.id, None)
        await callback.message.edit_text(
            _success_answer(request),
            parse_mode="HTML",
            reply_markup=None,
        )
    else:
        CONFIRMED_CREATE_USERS.discard(callback.from_user.id)
        PENDING_REQUEST_DRAFTS.pop(callback.from_user.id, None)
        await callback.message.edit_text(
            f"⚠️ <b>Не удалось передать обращение.</b>\n\n{escape(str(result['error']))}",
            parse_mode="HTML",
            reply_markup=None,
        )
    await callback.answer()


async def _handle_request_list(message: Message) -> None:
    if not message.from_user:
        answer = (
            "Я могу проверить заявки автоматически, но в этом чате не вижу ваш Telegram ID."
        )
        await _answer_html(message, answer)
        return

    requests = list_customer_requests_by_telegram(message.from_user.id)
    if not requests:
        answer = "У вас нет активных заявок."
        _remember(message, message.text or "", answer)
        await _answer_html(message, answer)
        return

    rows = "\n".join(
        f"{index}. {escape(_request_record_title(record))}"
        f"{_request_record_meta(record)}"
        for index, record in enumerate(requests, start=1)
    )
    answer = (
        "<b>Ваши активные обращения:</b>\n"
        f"{rows}"
    )
    _remember(message, message.text or "", answer)
    await _answer_html(message, answer)


async def _handle_cancel_request(message: Message) -> None:
    if not message.from_user:
        answer = (
            "Да, отмена заявки возможна.\n\n"
            "Но я не вижу ваш Telegram ID в этом чате и не могу безопасно найти заявку автоматически."
        )
        await _answer_html(message, answer)
        return

    requests = list_customer_requests_by_telegram(message.from_user.id)
    if not requests:
        answer = (
            "Да, отмена заявки возможна.\n\n"
            "Я проверил обращения по вашему Telegram ID и не нашел активных заявок для отмены."
        )
        _remember(message, message.text or "", answer)
        await _answer_html(message, answer)
        return

    if len(requests) == 1:
        await _send_cancel_confirmation(message, requests[0])
        return

    PENDING_CANCEL_SELECTIONS[message.from_user.id] = requests
    rows = "\n".join(
        f"{index}. {escape(_request_record_title(record))}"
        for index, record in enumerate(requests, start=1)
    )
    answer = (
        "Да, отмена заявки возможна.\n\n"
        "<b>Ваши обращения:</b>\n"
        f"{rows}\n\n"
        "В ответном сообщении напишите только цифру заявки, которую необходимо отменить."
    )
    _remember(message, message.text or "", answer)
    await _answer_html(message, answer)


async def _handle_cancel_selection(message: Message, selected_number: int) -> None:
    if not message.from_user:
        return

    requests = PENDING_CANCEL_SELECTIONS.get(message.from_user.id, [])
    if selected_number < 1 or selected_number > len(requests):
        answer = "Пожалуйста, напишите цифру из списка заявок."
        await _answer_html(message, answer)
        return

    record = requests[selected_number - 1]
    PENDING_CANCEL_SELECTIONS.pop(message.from_user.id, None)
    await _send_cancel_confirmation(message, record)


async def _send_cancel_confirmation(message: Message, record: dict[str, str]) -> None:
    if not message.from_user:
        return

    PENDING_CANCEL_CONFIRMATIONS[message.from_user.id] = record
    title = escape(_request_record_title(record))
    created_at = escape(record.get("Дата и время", "").strip() or "дата не указана")
    answer = (
        f"Заявка <b>«{title}»</b>\n"
        f"Дата создания: {created_at}\n\n"
        "Вы действительно хотите отменить?"
    )
    await message.answer(
        answer,
        parse_mode="HTML",
        reply_markup=_cancel_confirmation_keyboard(),
    )


@router.callback_query(F.data.in_({"cancel_request_yes", "cancel_request_no"}))
async def handle_cancel_confirmation(callback: CallbackQuery) -> None:
    if not callback.from_user:
        await callback.answer()
        return
    if not callback.message:
        await callback.answer("Не удалось обновить сообщение.", show_alert=True)
        return

    record = PENDING_CANCEL_CONFIRMATIONS.pop(callback.from_user.id, None)
    if not record:
        await callback.answer("Заявка для отмены не выбрана.", show_alert=True)
        return

    if callback.data == "cancel_request_no":
        await callback.message.edit_text(
            "Хорошо, заявку не отменяю.",
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.answer()
        return

    title = _request_record_title(record)
    row_number = int(record.get("_row_number", "0") or 0)
    if row_number <= 0:
        await callback.message.edit_text(
            "⚠️ <b>Не удалось отменить заявку.</b>\n\nЗаявка не найдена в таблице.",
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.answer()
        return

    result = cancel_customer_request(
        row_number,
        comment=f"Отменено клиентом через Telegram ID {callback.from_user.id}",
    )
    if result["success"]:
        await callback.message.edit_text(
            f"✅ Заявка <b>«{escape(title)}»</b> отменена.",
            parse_mode="HTML",
            reply_markup=None,
        )
    else:
        await callback.message.edit_text(
            f"⚠️ <b>Не удалось отменить заявку.</b>\n\n{escape(str(result['error']))}",
            parse_mode="HTML",
            reply_markup=None,
        )
    await callback.answer()


def _has_pending_cancel_selection(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in PENDING_CANCEL_SELECTIONS)


def _is_cancel_request(text: str) -> bool:
    normalized = text.lower().replace("ё", "е")
    cancel_markers = ("отмен", "удалить", "аннулир")
    request_markers = ("заявк", "обращен", "запис")
    return any(marker in normalized for marker in cancel_markers) and any(
        marker in normalized for marker in request_markers
    )


def _is_request_list_question(text: str) -> bool:
    normalized = text.lower().replace("ё", "е")
    if _is_new_service_booking_phrase(normalized):
        return False

    request_markers = ("заявк", "обращен", "запис")
    list_markers = (
        "есть",
        "мои",
        "моя",
        "еще",
        "другие",
        "какие",
        "покажи",
        "провер",
        "найди",
    )
    return any(marker in normalized for marker in request_markers) and any(
        marker in normalized for marker in list_markers
    )


def _is_new_service_booking_phrase(normalized: str) -> bool:
    booking_markers = ("хочу запис", "записаться", "запишите", "нужно запис", "можно запис")
    service_markers = ("сервис", "то", "диагност", "ремонт", "визит")
    return (
        any(marker in normalized for marker in booking_markers)
        and any(marker in normalized for marker in service_markers)
    )


def _is_explicit_create_request(text: str) -> bool:
    normalized = text.lower().replace("ё", "е")
    create_markers = ("создать", "оформить", "оставить", "подать", "сделать")
    request_markers = ("заявк", "обращен")
    return any(marker in normalized for marker in create_markers) and any(
        marker in normalized for marker in request_markers
    )


def _is_consultation_question(text: str, request: CustomerRequest) -> bool:
    normalized = text.strip().lower().replace("ё", "е")
    if request.has_contact():
        return False
    if request.request_type in {"general", "status"}:
        return False
    if request.request_type in {"warranty", "diagnostics", "repair"}:
        return True

    question_markers = (
        "?",
        "если ",
        "это ",
        "можно ли",
        "как ",
        "что ",
        "почему ",
        "когда ",
        "сколько ",
        "является ли",
    )
    return any(marker in normalized for marker in question_markers)


def _prepare_consultation_response(
    answer: str,
    user_text: str,
    request: CustomerRequest,
    user_id: int | None,
) -> tuple[str, InlineKeyboardMarkup | None]:
    answer = _remove_premature_data_request(answer)
    answer = _format_specialist_offer(answer)
    answer = _join_split_specialist_offer(answer)
    answer = _add_consultation_emojis(answer, request)
    should_offer_request = _should_offer_request_from_consultation(answer, request, user_text)

    if should_offer_request and not _has_specialist_offer(answer):
        answer = (
            f"{answer}\n\n"
            "📝 Если хотите, я могу подготовить заявку для обратной связи со специалистом."
        )

    reply_markup = None
    if should_offer_request and user_id:
        PENDING_CREATE_CONFIRMATIONS[user_id] = request
        reply_markup = _consultation_request_keyboard()

    return answer, reply_markup


def _remove_premature_data_request(answer: str) -> str:
    patterns = (
        r"\s*Пожалуйста,\s+напишите\s+следующие\s+данные:\s*(?:\n\s*•[^\n]*)+",
        r"\s*Для\s+этого\s+напишите,\s+пожалуйста:\s*(?:\n\s*•[^\n]*)+",
        r"\s*Для\s+заявки\s+напишите,\s+пожалуйста:\s*(?:\n\s*•[^\n]*)+",
    )
    cleaned = answer
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _format_specialist_offer(answer: str) -> str:
    return re.sub(
        r"(?<!\n)\s+(Я могу передать)",
        r"\n\n\1",
        answer,
        flags=re.IGNORECASE,
    )


def _join_split_specialist_offer(answer: str) -> str:
    answer = re.sub(
        r"(Если хотите,)\s*\n+\s*(я могу)",
        r"\1 я могу",
        answer,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\n{3,}", "\n\n", answer).strip()


def _add_consultation_emojis(answer: str, request: CustomerRequest) -> str:
    if not answer:
        return answer

    prefix_by_type = {
        "warranty": "🛡️",
        "diagnostics": "🛠️",
        "repair": "🔧",
        "maintenance": "🔧",
        "service_booking": "📝",
    }
    prefix = prefix_by_type.get(request.request_type, "💬")
    if not answer.startswith(tuple(prefix_by_type.values())) and not answer.startswith(("💬", "📝", "⚠️", "✅", "🔎")):
        answer = f"{prefix} {answer}"

    answer = re.sub(
        r"(?m)^(Если хотите,\s+я могу)",
        r"📝 \1",
        answer,
        flags=re.IGNORECASE,
    )
    return answer


def _should_offer_request_from_consultation(answer: str, request: CustomerRequest, user_text: str) -> bool:
    if request.request_type not in {"warranty", "diagnostics", "repair", "maintenance", "service_booking"}:
        return False
    if request.request_type in {"warranty", "diagnostics", "repair"} and _has_specialist_offer(answer):
        return True
    if not _has_request_creation_intent(user_text):
        return False
    return _has_specialist_offer(answer) or not _has_request_reference(answer)


def _has_request_creation_intent(text: str) -> bool:
    normalized = text.lower().replace("ё", "е")
    markers = (
        "хочу запис",
        "записаться",
        "запишите",
        "создать заяв",
        "создать обращ",
        "оформить заяв",
        "оформить обращ",
        "оставить заяв",
        "оставить обращ",
        "подать заяв",
        "передать специалист",
        "связаться со специалист",
        "нужен специалист",
        "нужна консультация специалист",
    )
    return any(marker in normalized for marker in markers)


def _has_specialist_offer(answer: str) -> bool:
    lowered = answer.lower()
    return (
        "могу передать" in lowered
        or "могу подготовить заявку" in lowered
        or "могу подготовить обращение" in lowered
        or "предлож" in lowered and "специалист" in lowered
    )


def _has_request_reference(answer: str) -> bool:
    lowered = answer.lower()
    return "заяв" in lowered or "обращен" in lowered


def _request_record_title(record: dict[str, str]) -> str:
    description = record.get("Описание обращения", "").strip()
    request_type = record.get("Тип обращения", "").strip()
    fallback_titles = {
        "warranty": "Гарантийное обращение",
        "service_booking": "Запись на сервис",
        "maintenance": "Вопрос по техническому обслуживанию",
        "diagnostics": "Запрос на диагностику",
        "repair": "Обращение по ремонту",
    }
    return description or fallback_titles.get(request_type, "Общее обращение")


def _request_record_meta(record: dict[str, str]) -> str:
    parts = []
    created_at = record.get("Дата и время", "").strip()
    status = record.get("Статус", "").strip()
    if created_at:
        parts.append(f"создана {escape(created_at)}")
    if status:
        status_title, _ = _human_status(status)
        clean_status = re.sub(r"<[^>]+>", "", status_title)
        clean_status = clean_status.lstrip("✅🕒🛠️ ").strip(".")
        parts.append(escape(clean_status.lower()))
    return f" ({', '.join(parts)})" if parts else ""


def _plural_requests(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} активная заявка"
    if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        return f"{count} активные заявки"
    return f"{count} активных заявок"


def _create_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Создать", callback_data="create_request_yes"),
                InlineKeyboardButton(text="Отмена", callback_data="create_request_no"),
            ]
        ]
    )


def _consultation_request_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Создать заявку", callback_data="create_request_yes"),
                InlineKeyboardButton(text="Отмена", callback_data="create_request_no"),
            ]
        ]
    )


def _cancel_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data="cancel_request_yes"),
                InlineKeyboardButton(text="Нет", callback_data="cancel_request_no"),
            ]
        ]
    )


def _missing_fields_answer(request: CustomerRequest, missing: list[str]) -> str:
    if len(missing) > 5:
        missing = missing[:5]

    if request.request_type == "warranty":
        intro = (
            "🔎 Сразу подтвердить, что поломка гарантийная, нельзя: это проверяет "
            "гарантийный специалист по данным автомобиля и описанию ситуации."
        )
        action = "📝 Чтобы передать вопрос на проверку, напишите, пожалуйста:"
    elif request.request_type in {"diagnostics", "repair"}:
        intro = (
            "🛠️ Понял ситуацию. Я могу подготовить заявку и передать описание "
            "специалисту сервиса."
        )
        action = "📝 Для этого напишите, пожалуйста:"
    elif request.request_type == "service_booking":
        intro = (
            "📝 Хорошо, помогу подготовить заявку на сервис.\n"
            "Дату и время визита "
            "подтвердит специалист."
        )
        action = "Для заявки напишите, пожалуйста:"
    else:
        intro = "📝 Хорошо, я могу подготовить обращение для специалиста."
        action = "Для этого напишите, пожалуйста:"

    missing_list = "\n".join(f"• {escape(item)}" for item in missing)

    return (
        f"{intro}\n\n"
        f"{action}\n"
        f"{missing_list}"
    )


def _success_answer(request: CustomerRequest) -> str:
    title = _success_title(request.request_type)
    summary = _request_summary(request)
    details = f"\n\n📋 <b>Данные заявки:</b>\n{summary}" if summary else ""

    if request.request_type == "warranty":
        note = (
            "Гарантийный специалист проверит информацию и подскажет дальнейшие действия. "
            "Сам гарантийный статус подтвердят только после проверки. "
            "Специалист свяжется с вами в ближайшее время."
        )
    elif request.request_type == "service_booking":
        note = "📞 Специалист свяжется с вами в ближайшее время для подтверждения записи."
    else:
        note = "📞 Специалист свяжется с вами в ближайшее время для проверки деталей и дальнейших действий."

    return f"✅ <b>{title}</b>{details}\n\n{note}"


def _success_title(request_type: str) -> str:
    titles = {
        "warranty": "Гарантийное обращение передано специалисту",
        "service_booking": "Заявка на сервис передана специалисту",
        "maintenance": "Вопрос по ТО передан специалисту",
        "diagnostics": "Описание ситуации передано специалисту",
        "repair": "Обращение по ремонту передано специалисту",
    }
    return titles.get(request_type, "Обращение передано специалисту")


def _request_summary(request: CustomerRequest) -> str:
    rows = [
        ("Имя", request.name),
        ("Телефон", _mask_contact(request.phone)),
        ("Email", _mask_contact(request.email)),
        ("Автомобиль", " ".join(part for part in [request.brand, request.model] if part)),
        ("Год выпуска", request.year),
        ("Пробег", f"{request.mileage} км" if request.mileage else ""),
        ("VIN / госномер", _mask_vehicle_id(request.vin_or_plate)),
        ("Описание", _description_for_sheet(request)),
        ("Желаемое время визита", request.preferred_visit_time),
    ]
    return "\n".join(
        f"• <b>{label}:</b> {escape(value)}"
        for label, value in rows
        if value
    )


def _mask_contact(value: str) -> str:
    if not value:
        return ""
    return "[контакт скрыт]"


def _mask_vehicle_id(value: str) -> str:
    if not value:
        return ""
    return "[номер скрыт]"


def _human_join(items: list[str]) -> str:
    if len(items) <= 1:
        return items[0] if items else ""
    return f"{', '.join(items[:-1])} и {items[-1]}"


def _merge_request_draft(draft: CustomerRequest, update: CustomerRequest) -> CustomerRequest:
    merged = CustomerRequest(**draft.to_dict())
    for field_name in (
        "name",
        "phone",
        "email",
        "brand",
        "model",
        "year",
        "mileage",
        "vin_or_plate",
        "preferred_visit_time",
    ):
        value = getattr(update, field_name)
        if value:
            setattr(merged, field_name, value)

    if update.request_type != "general":
        merged.request_type = update.request_type
        merged.topic = update.topic
    elif merged.request_type == "general" and _looks_like_service_details(update.description):
        merged.request_type = "service_booking"
        merged.topic = "Запись на сервис"

    if _is_meaningful_description(update.description) and not _is_contact_details_only(update):
        merged.description = _merge_description(merged.description, update.description)

    merged.priority = "high" if merged.request_type in {"warranty", "diagnostics", "repair"} else "normal"
    return merged


def _merge_description(existing: str, new: str) -> str:
    existing = existing.strip()
    new = new.strip()
    if not _is_meaningful_description(existing):
        return new
    if new.lower() in existing.lower():
        return existing
    return f"{existing}; {new}"


def _looks_like_service_details(text: str) -> bool:
    normalized = text.lower().replace("ё", "е")
    markers = ("консультац", "техническ", "обслуж", "то", "сервис", "визит")
    return any(marker in normalized for marker in markers)


def _is_meaningful_description(text: str) -> bool:
    normalized = text.strip().lower().replace("ё", "е")
    if not normalized:
        return False
    generic_patterns = (
        "хочу создать заявку",
        "создать заявку",
        "хочу оставить заявку",
        "оставить заявку",
        "оформить заявку",
        "подать заявку",
        "хочу создать обращение",
        "создать обращение",
    )
    return not any(pattern in normalized for pattern in generic_patterns)


def _is_contact_details_only(request: CustomerRequest) -> bool:
    text = request.description.strip()
    if not text:
        return False

    remainder = text
    for value in (
        request.name,
        request.phone,
        request.email,
        request.brand,
        request.model,
        request.year,
        request.mileage,
        request.vin_or_plate,
    ):
        if value:
            remainder = re.sub(re.escape(value), " ", remainder, flags=re.IGNORECASE)

    remainder = re.sub(r"[,.;:()\\-]+", " ", remainder)
    words = re.findall(r"[а-яёa-z]+", remainder.lower(), flags=re.IGNORECASE)
    filler = {"и", "мой", "моя", "меня", "зовут", "телефон", "email", "почта", "авто", "машина"}
    return not any(word not in filler for word in words)


def _is_work_schedule_question(text: str) -> bool:
    normalized = text.lower().replace("ё", "е")
    schedule_markers = (
        "работает",
        "работаете",
        "открыт",
        "открыты",
        "график",
        "режим работы",
        "часы работы",
        "до скольки",
        "во сколько",
    )
    place_markers = ("салон", "сервис", "центр", "вы", "автосервис")
    day_markers = (
        "вс",
        "воскрес",
        "сб",
        "суббот",
        "будни",
        "понедель",
        "вторник",
        "сред",
        "четверг",
        "пятниц",
    )
    return (
        any(marker in normalized for marker in schedule_markers)
        and (
            any(marker in normalized for marker in place_markers)
            or any(_has_day_marker(normalized, marker) for marker in day_markers)
        )
    )


def _has_day_marker(text: str, marker: str) -> bool:
    if marker in {"вс", "сб"}:
        return re.search(rf"\b{marker}\b", text) is not None
    return marker in text


def _work_schedule_answer(text: str) -> str:
    normalized = text.lower().replace("ё", "е")
    asks_sunday = "воскрес" in normalized or re.search(r"\bвс\b", normalized)

    if asks_sunday:
        return (
            "🕒 <b>В воскресенье сервисный центр не работает.</b>\n\n"
            "График работы:\n"
            "• понедельник-пятница: 09:00-20:00\n"
            "• суббота: 10:00-18:00\n"
            "• воскресенье: выходной\n\n"
            "📩 Обращение через бота можно оставить в любое время. Специалист обработает его в рабочие часы."
        )

    return (
        "🕒 <b>График работы сервисного центра</b>\n\n"
        "• понедельник-пятница: 09:00-20:00\n"
        "• суббота: 10:00-18:00\n"
        "• воскресенье: выходной\n\n"
        "📩 Обращение через бота можно оставить в любое время."
    )


async def _handle_status(message: Message, request: CustomerRequest) -> None:
    if not request.has_lookup_key() and not message.from_user:
        answer = (
            "🔎 <b>Проверка статуса</b>\n\n"
            "Для поиска обращения нужен телефон, VIN или госномер, указанный в заявке."
        )
        _remember(message, request.description, answer)
        await _answer_html(message, answer)
        return

    record = find_customer_request(
        phone=request.phone,
        vin_or_plate=request.vin_or_plate,
        telegram_id=message.from_user.id if message.from_user else None,
    )

    if not record:
        answer = (
            "🔎 <b>Проверка статуса</b>\n\n"
            "Я не нашел обращение по указанным данным.\n\n"
            "Могу передать вопрос специалисту, если вы напишете имя, телефон, автомобиль и суть обращения."
        )
        _remember(message, request.description, answer)
        await _answer_html(message, answer)
        return

    raw_status = str(record.get("Статус", "")).strip()
    status_title, status_text = _human_status(raw_status)
    comment = escape(record.get("Комментарий специалиста", ""))
    answer = (
        f"🔎 <b>Статус обращения</b>\n\n"
        f"{status_title}\n"
        f"{status_text}"
        + (f"\n\n💬 <b>Комментарий специалиста:</b>\n{comment}" if comment else "")
    )
    _remember(message, request.description, answer)
    await _answer_html(message, answer)


def _human_status(raw_status: str) -> tuple[str, str]:
    normalized = raw_status.lower().strip()
    statuses = {
        "new": (
            "🕒 <b>Ваша заявка находится на рассмотрении.</b>",
            "Специалист получил обращение и свяжется с вами в ближайшее время.",
        ),
        "in_progress": (
            "🛠️ <b>Ваша заявка в работе.</b>",
            "Специалист уже занимается обращением и свяжется с вами, когда появится уточнение.",
        ),
        "processing": (
            "🛠️ <b>Ваша заявка в работе.</b>",
            "Специалист уже занимается обращением и свяжется с вами, когда появится уточнение.",
        ),
        "done": (
            "✅ <b>Заявка обработана.</b>",
            "Если потребуется дополнительная информация, специалист свяжется с вами отдельно.",
        ),
        "closed": (
            "✅ <b>Обращение закрыто.</b>",
            "Если вопрос ещё актуален, можно оставить новое обращение.",
        ),
        "cancelled": (
            "✅ <b>Заявка отменена.</b>",
            "Если вопрос снова станет актуален, можно оставить новую заявку.",
        ),
        "canceled": (
            "✅ <b>Заявка отменена.</b>",
            "Если вопрос снова станет актуален, можно оставить новую заявку.",
        ),
    }
    return statuses.get(
        normalized,
        (
            "🕒 <b>Ваша заявка находится на рассмотрении.</b>",
            "Специалист проверит информацию и свяжется с вами в ближайшее время.",
        ),
    )


def _missing_required_fields(request: CustomerRequest) -> list[str]:
    missing: list[str] = []
    if not request.name:
        missing.append("имя")
    if not request.has_contact():
        missing.append("телефон или email")
    if not (request.brand or request.model):
        missing.append("марка или модель автомобиля")
    if not _is_meaningful_description(request.description):
        missing.append("краткое описание ситуации")
    if request.request_type == "service_booking" and not request.preferred_visit_time:
        missing.append("удобный день или период для визита")
    return missing


def _apply_dialog_request_intent(message: Message, request: CustomerRequest) -> None:
    if request.request_type != "general":
        return
    if not (request.has_contact() and (request.brand or request.model)):
        return

    messages = dialog_memory.get_messages(message.from_user.id if message.from_user else None)
    last_assistant_text = ""
    for item in reversed(messages):
        if item.get("role") == "assistant":
            last_assistant_text = item.get("content", "").lower()
            break

    if "заявк" in last_assistant_text and "сервис" in last_assistant_text:
        request.request_type = "service_booking"
        request.topic = "Запись на сервис"


def _remember(message: Message, user_text: str, assistant_text: str) -> None:
    dialog_memory.add_pair(
        message.from_user.id if message.from_user else None,
        user_text,
        assistant_text,
    )


def _is_admin(message: Message) -> bool:
    if not message.from_user or not settings.admin_telegram_id:
        return False
    return str(message.from_user.id) == settings.admin_telegram_id


async def _answer_html(message: Message, text: str) -> None:
    await message.answer(text, parse_mode="HTML")


def _safe_html(text: str) -> str:
    safe = (
        escape(text)
        .replace("&lt;b&gt;", "<b>")
        .replace("&lt;/b&gt;", "</b>")
    )
    return _markdown_bold_to_html(safe)


def _markdown_bold_to_html(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
