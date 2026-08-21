import os


class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "admin")
    secret_key: str = os.getenv("SECRET_KEY", "change-me-please")
    database_url: str = os.getenv(
        "DATABASE_URL", "sqlite+aiosqlite:///./sendbot.db"
    )
    # сообщений в секунду при рассылке (лимит ТГ ~30/с, держим запас)
    broadcast_rate: float = float(os.getenv("BROADCAST_RATE", "20"))
    # папка с картинками (из .docx и своими)
    media_dir: str = os.getenv("MEDIA_DIR", "./media")
    # автоочистка истории переписки и получателей рассылок старше N дней
    # (0 = хранить вечно). На больших базах помогает держать БД компактной.
    history_retention_days: int = int(os.getenv("HISTORY_RETENTION_DAYS", "0"))


settings = Settings()
