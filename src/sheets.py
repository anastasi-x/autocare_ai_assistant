from datetime import datetime, timedelta, timezone
import logging
import re

import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials

from src.config import settings
from src.customer_parser import CustomerRequest


logger = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
MOSCOW_TZ = timezone(timedelta(hours=3), "MSK")
GENERIC_DESCRIPTION_WORDS = {
    "хочу",
    "записаться",
    "запись",
    "заявка",
    "заявку",
    "на",
    "в",
    "сервис",
    "сервиса",
    "пожалуйста",
}

CUSTOMER_REQUEST_HEADERS = [
    "Дата и время",
    "Telegram ID",
    "Username",
    "Имя клиента",
    "Телефон",
    "Email",
    "Марка",
    "Модель",
    "Год выпуска",
    "Пробег",
    "VIN / госномер",
    "Тип обращения",
    "Описание обращения",
    "Желаемое время визита",
    "Приоритет",
    "Статус",
    "Комментарий специалиста",
]

SERVICE_TOPICS_HEADERS = [
    "Категория",
    "Описание",
    "Какие данные собрать",
    "Ответственный отдел",
    "Приоритет по умолчанию",
    "Когда передавать специалисту",
    "Статус",
]


def _sheet() -> gspread.Spreadsheet:
    credentials = Credentials.from_service_account_file(
        settings.google_service_account_file,
        scopes=SCOPES,
    )
    client = gspread.authorize(credentials)
    return client.open_by_key(settings.google_sheet_id)


def _worksheet(name: str, headers: list[str]) -> gspread.Worksheet:
    spreadsheet = _sheet()
    try:
        worksheet = spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=name, rows=1000, cols=len(headers))

    existing_values = worksheet.row_values(1)
    if existing_values != headers:
        worksheet.update([headers], "A1")
    return worksheet


def save_request_to_sheet(
    request: CustomerRequest,
    telegram_id: int | None = None,
    username: str = "",
) -> dict[str, str | bool]:
    if not settings.google_sheet_id:
        return {
            "success": False,
            "error": "Не удалось передать обращение специалисту. Таблица не настроена.",
        }

    try:
        worksheet = _worksheet(
            settings.google_customer_requests_worksheet,
            CUSTOMER_REQUEST_HEADERS,
        )
        worksheet.append_row(
            [
                _moscow_timestamp(),
                str(telegram_id or ""),
                username,
                request.name,
                request.phone,
                request.email,
                request.brand,
                request.model,
                request.year,
                request.mileage,
                request.vin_or_plate,
                request.request_type,
                _description_for_sheet(request),
                request.preferred_visit_time,
                request.priority,
                request.status,
                "",
            ],
            value_input_option="USER_ENTERED",
        )
        return {"success": True, "error": ""}
    except APIError as exc:
        logger.exception("Google Sheets API error while saving customer request")
        return {
            "success": False,
            "error": _friendly_sheets_error(exc),
        }
    except PermissionError as exc:
        logger.exception("Google Sheets permission error while saving customer request")
        if isinstance(exc.__cause__, APIError):
            return {
                "success": False,
                "error": _friendly_sheets_error(exc.__cause__),
            }
        return {
            "success": False,
            "error": "Не удалось передать обращение специалисту. Таблица сейчас недоступна для бота.",
        }
    except Exception:
        logger.exception("Failed to save customer request to Google Sheets")
        return {
            "success": False,
            "error": "Не удалось передать обращение специалисту. Попробуйте позже.",
        }


def load_service_topics() -> list[dict[str, str]]:
    if not settings.google_sheet_id:
        return []

    try:
        worksheet = _worksheet(settings.google_service_topics_worksheet, SERVICE_TOPICS_HEADERS)
        return worksheet.get_all_records()
    except Exception:
        return []


def find_customer_request(
    *,
    phone: str = "",
    vin_or_plate: str = "",
    telegram_id: int | None = None,
) -> dict[str, str] | None:
    if not settings.google_sheet_id:
        return None

    try:
        worksheet = _worksheet(
            settings.google_customer_requests_worksheet,
            CUSTOMER_REQUEST_HEADERS,
        )
        records = worksheet.get_all_records()
    except Exception:
        return None

    for record in reversed(records):
        same_phone = phone and str(record.get("Телефон", "")).strip() == phone
        same_vehicle_id = vin_or_plate and str(record.get("VIN / госномер", "")).strip().lower() == vin_or_plate.lower()
        same_telegram = telegram_id and str(record.get("Telegram ID", "")).strip() == str(telegram_id)
        if same_phone or same_vehicle_id or same_telegram:
            return {str(key): str(value) for key, value in record.items()}

    return None


def list_customer_requests_by_telegram(telegram_id: int | None) -> list[dict[str, str]]:
    if not settings.google_sheet_id or not telegram_id:
        return []

    try:
        worksheet = _worksheet(
            settings.google_customer_requests_worksheet,
            CUSTOMER_REQUEST_HEADERS,
        )
        records = worksheet.get_all_records()
    except Exception:
        logger.exception("Failed to load customer requests from Google Sheets")
        return []

    result: list[dict[str, str]] = []
    for row_number, record in enumerate(records, start=2):
        same_telegram = str(record.get("Telegram ID", "")).strip() == str(telegram_id)
        if not same_telegram:
            continue

        normalized_status = str(record.get("Статус", "")).strip().lower()
        if normalized_status in {"cancelled", "canceled", "closed", "done"}:
            continue

        normalized_record = {str(key): str(value) for key, value in record.items()}
        normalized_record["_row_number"] = str(row_number)
        result.append(normalized_record)

    return list(reversed(result))


def cancel_customer_request(row_number: int, comment: str = "") -> dict[str, str | bool]:
    if not settings.google_sheet_id:
        return {
            "success": False,
            "error": "Не удалось отменить заявку. Таблица не настроена.",
        }

    try:
        worksheet = _worksheet(
            settings.google_customer_requests_worksheet,
            CUSTOMER_REQUEST_HEADERS,
        )
        status_col = CUSTOMER_REQUEST_HEADERS.index("Статус") + 1
        comment_col = CUSTOMER_REQUEST_HEADERS.index("Комментарий специалиста") + 1
        worksheet.update_cell(row_number, status_col, "cancelled")
        if comment:
            worksheet.update_cell(row_number, comment_col, comment)
        return {"success": True, "error": ""}
    except Exception:
        logger.exception("Failed to cancel customer request in Google Sheets")
        return {
            "success": False,
            "error": "Не удалось отменить заявку. Попробуйте позже.",
        }


def _moscow_timestamp() -> str:
    return datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _description_for_sheet(request: CustomerRequest) -> str:
    description = request.description.strip()
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
            description = re.sub(re.escape(value), " ", description, flags=re.IGNORECASE)

    description = re.sub(
        r"\b(меня зовут|мо[её] имя|имя|телефон|email|почта|год|года)\b",
        " ",
        description,
        flags=re.IGNORECASE,
    )
    description = re.sub(r"\s+", " ", description)

    description = " ".join(
        part.strip(" ,.;:-")
        for part in description.split()
        if part.strip(" ,.;:-")
    )
    if _is_generic_description(description):
        return _default_description(request)
    return description or _default_description(request)


def _is_generic_description(description: str) -> bool:
    words = {word.lower() for word in re.findall(r"[а-яёa-z]+", description, re.IGNORECASE)}
    return bool(words) and words.issubset(GENERIC_DESCRIPTION_WORDS)


def _default_description(request: CustomerRequest) -> str:
    descriptions = {
        "warranty": "Гарантийное обращение",
        "service_booking": "Запись на сервис",
        "maintenance": "Вопрос по техническому обслуживанию",
        "diagnostics": "Запрос на диагностику",
        "repair": "Обращение по ремонту",
    }
    return descriptions.get(request.request_type, request.topic or "Общее обращение")


def _friendly_sheets_error(exc: APIError) -> str:
    message = str(exc)
    if "sheets.googleapis.com" in message and "disabled" in message.lower():
        return (
            "Не удалось передать обращение специалисту: для проекта Google Cloud "
            "не включен Google Sheets API."
        )
    if "[403]" in message:
        return (
            "Не удалось передать обращение специалисту: у бота нет доступа к таблице "
            "или к Google Sheets API."
        )
    return "Не удалось передать обращение специалисту. Таблица сейчас недоступна."
