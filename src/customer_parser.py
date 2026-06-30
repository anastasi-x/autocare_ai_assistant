import re
from dataclasses import asdict, dataclass


PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{7,}\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
YEAR_RE = re.compile(r"\b(19[8-9]\d|20[0-3]\d)\b")
MILEAGE_RE = re.compile(r"(\d[\d\s]{2,7})\s*(?:км|km|тыс\.?\s*км|пробег)", re.IGNORECASE)
VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
PLATE_RE = re.compile(r"\b[АВЕКМНОРСТУХABEKMHOPCTYX]\s?\d{3}\s?[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s?\d{2,3}\b", re.IGNORECASE)
NAME_RE = re.compile(
    r"(?:меня зовут|мо[её] имя|имя)\s*[:\-]?\s+([А-ЯЁA-Z][а-яёa-z-]{1,30})",
    re.IGNORECASE,
)
LEADING_NAME_RE = re.compile(
    r"^\s*([А-ЯЁA-Z][а-яёa-z-]{1,30})(?:\s*,|\s+(?=\+?\d|[\w.+-]+@))",
    re.IGNORECASE,
)
VISIT_RE = re.compile(
    r"\b("
    r"(?:сегодня|завтра|послезавтра|на выходных|на неделе|"
    r"понедельник|вторник|среду|среда|четверг|пятницу|пятница|субботу|суббота|"
    r"воскресенье|\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)"
    r"(?:\s+(?:после|до|к|в|с)\s*\d{1,2}(?::\d{2})?)?"
    r"|утром|днем|днём|вечером"
    r")\b",
    re.IGNORECASE,
)

REQUEST_KEYWORDS = {
    "warranty": ("гарант", "по гарантии", "гарантий"),
    "service_booking": ("запис", "сервис", "прием", "приём", "визит"),
    "maintenance": ("то", "техническое обслуживание", "регламент"),
    "diagnostics": (
        "диагност",
        "ошибка",
        "горит",
        "стук",
        "шум",
        "вибрац",
        "запах",
        "дым",
        "бензин",
        "капот",
        "течь",
        "перегрев",
        "неисправ",
    ),
    "repair": ("ремонт", "почин", "замен"),
    "status": ("статус", "готов", "что с обращением", "номер заявки"),
}

KNOWN_BRANDS = (
    "Audi",
    "BMW",
    "Chery",
    "Chevrolet",
    "Ford",
    "Geely",
    "Haval",
    "Honda",
    "Hyundai",
    "Kia",
    "Lada",
    "Lexus",
    "Mazda",
    "Mercedes",
    "Mitsubishi",
    "Nissan",
    "Renault",
    "Skoda",
    "Toyota",
    "Volkswagen",
    "Volvo",
)


@dataclass
class CustomerRequest:
    name: str = ""
    phone: str = ""
    email: str = ""
    brand: str = ""
    model: str = ""
    year: str = ""
    mileage: str = ""
    vin_or_plate: str = ""
    request_type: str = "general"
    topic: str = ""
    description: str = ""
    preferred_visit_time: str = ""
    priority: str = "normal"
    status: str = "new"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    def has_contact(self) -> bool:
        return bool(self.phone or self.email)

    def has_lookup_key(self) -> bool:
        return bool(self.phone or self.vin_or_plate)


def detect_request_type(text: str) -> str:
    lowered = text.lower()
    for request_type, keywords in REQUEST_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return request_type
    return "general"


def is_customer_request(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "заявк",
        "обращен",
        "специалист",
        "запис",
        "гарант",
        "диагност",
        "ремонт",
        "статус",
    )
    return any(marker in lowered for marker in markers) or bool(PHONE_RE.search(text))


def _extract_brand_and_model(text: str) -> tuple[str, str]:
    for brand in KNOWN_BRANDS:
        match = re.search(rf"\b{re.escape(brand)}\b\s*([A-Za-zА-Яа-яЁё0-9-]{{1,30}})?", text, re.IGNORECASE)
        if match:
            return brand, (match.group(1) or "").strip(" ,.;")
    return "", ""


def _extract_name(text: str) -> str:
    explicit_match = NAME_RE.search(text)
    if explicit_match:
        return explicit_match.group(1).strip()

    leading_match = LEADING_NAME_RE.search(text)
    if leading_match and (PHONE_RE.search(text) or EMAIL_RE.search(text)):
        return leading_match.group(1).strip()

    return ""


def parse_customer_request(text: str) -> CustomerRequest:
    phone_match = PHONE_RE.search(text)
    email_match = EMAIL_RE.search(text)
    name = _extract_name(text)
    year_match = YEAR_RE.search(text)
    mileage_match = MILEAGE_RE.search(text)
    vin_match = VIN_RE.search(text)
    plate_match = PLATE_RE.search(text)
    visit_match = VISIT_RE.search(text)
    brand, model = _extract_brand_and_model(text)
    request_type = detect_request_type(text)

    priority = "high" if request_type in {"warranty", "diagnostics", "repair"} else "normal"

    return CustomerRequest(
        name=name,
        phone=phone_match.group(1).strip() if phone_match else "",
        email=email_match.group(0).strip() if email_match else "",
        brand=brand,
        model=model,
        year=year_match.group(1) if year_match else "",
        mileage=mileage_match.group(1).strip() if mileage_match else "",
        vin_or_plate=(vin_match.group(0) if vin_match else plate_match.group(0) if plate_match else "").strip(),
        request_type=request_type,
        topic=_topic_for_type(request_type),
        description=text.strip(),
        preferred_visit_time=visit_match.group(0).strip() if visit_match else "",
        priority=priority,
    )


def _topic_for_type(request_type: str) -> str:
    topics = {
        "warranty": "Гарантийное обращение",
        "service_booking": "Запись на сервис",
        "maintenance": "Техническое обслуживание",
        "diagnostics": "Диагностика",
        "repair": "Ремонт",
        "status": "Проверка статуса обращения",
        "general": "Общее обращение",
    }
    return topics.get(request_type, topics["general"])
