from dataclasses import dataclass
from pathlib import Path

import os

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(path: Path) -> None:
        return None


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = os.getenv("BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", ""))
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_embedding_model: str = os.getenv(
        "OPENAI_EMBEDDING_MODEL",
        "text-embedding-3-small",
    )

    google_service_account_file: Path = BASE_DIR / os.getenv(
        "GOOGLE_SHEETS_CREDENTIALS_FILE",
        os.getenv(
            "GOOGLE_SERVICE_ACCOUNT_FILE",
            "credentials/google-service-account.json",
        ),
    )
    google_sheet_id: str = os.getenv(
        "GOOGLE_SHEETS_SPREADSHEET_ID",
        os.getenv("GOOGLE_SHEET_ID", ""),
    )
    google_customer_requests_worksheet: str = os.getenv(
        "GOOGLE_CUSTOMER_REQUESTS_WORKSHEET",
        "CustomerRequests",
    )
    google_service_topics_worksheet: str = os.getenv(
        "GOOGLE_SERVICE_TOPICS_WORKSHEET",
        "ServiceTopics",
    )
    google_sheet_worksheet: str = os.getenv(
        "GOOGLE_SHEET_WORKSHEET",
        "CustomerRequests",
    )

    admin_telegram_id: str = os.getenv(
        "ADMIN_TELEGRAM_ID",
        os.getenv("TELEGRAM_ADMIN_ID", ""),
    )
    rate_limit_messages: int = int(os.getenv("RATE_LIMIT_MESSAGES", "8"))
    rate_limit_window_seconds: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

    knowledge_base_dir: Path = BASE_DIR / os.getenv(
        "KNOWLEDGE_BASE_DIR",
        "knowledge_base",
    )
    metadata_file: Path = knowledge_base_dir / "_metadata.json"
    prompt_file: Path = BASE_DIR / os.getenv(
        "SYSTEM_PROMPT_PATH",
        "prompts/system_prompt.md",
    )
    chroma_dir: Path = BASE_DIR / os.getenv("CHROMA_DB_DIR", "storage/chroma")
    cache_file: Path = BASE_DIR / os.getenv("CACHE_FILE", "storage/cache.json")
    dialog_context_file: Path = BASE_DIR / os.getenv(
        "DIALOG_CONTEXT_FILE",
        "storage/dialog_context.json",
    )
    bot_log_file: Path = BASE_DIR / os.getenv("LOG_FILE", "storage/bot.log")
    interaction_log_db_file: Path = BASE_DIR / os.getenv(
        "INTERACTION_LOG_DB_FILE",
        "storage/logs.db",
    )
    log_user_id_secret: str = os.getenv("LOG_USER_ID_SECRET", "")

    # Deprecated aliases kept for compatibility with the first scaffold.
    legacy_google_service_account_file: Path = BASE_DIR / os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_FILE",
        "credentials/google-service-account.json",
    )


settings = Settings()
