import re


PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{7,}\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
PLATE_RE = re.compile(
    r"\b[АВЕКМНОРСТУХABEKMHOPCTYX]\s?\d{3}\s?[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s?\d{2,3}\b",
    re.IGNORECASE,
)
PASSPORT_RE = re.compile(
    r"\b(?:паспорт|серия|номер паспорта)\b|\b\d{4}\s?\d{6}\b",
    re.IGNORECASE,
)
BANK_RE = re.compile(
    r"\b(?:карта|счет|счёт|iban|cvc|cvv)\b|\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",
    re.IGNORECASE,
)
def contains_private_contact(text: str) -> bool:
    return bool(
        PHONE_RE.search(text)
        or EMAIL_RE.search(text)
        or VIN_RE.search(text)
        or PLATE_RE.search(text)
        or PASSPORT_RE.search(text)
        or BANK_RE.search(text)
    )


def should_skip_cache(text: str) -> bool:
    return contains_private_contact(text)


def sanitize_for_memory(text: str) -> str:
    sanitized = VIN_RE.sub("[VIN скрыт]", text)
    sanitized = PLATE_RE.sub("[госномер скрыт]", sanitized)
    sanitized = EMAIL_RE.sub("[email скрыт]", sanitized)
    sanitized = PHONE_RE.sub("[телефон скрыт]", sanitized)
    sanitized = PASSPORT_RE.sub("[паспортные данные скрыты]", sanitized)
    sanitized = BANK_RE.sub("[банковские данные скрыты]", sanitized)
    return sanitized.strip()
