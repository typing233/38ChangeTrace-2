import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATA_DIR: str = "./data"

    # Auth
    AUTH_ENABLED: bool = False
    AUTH_SECRET_KEY: str = "changetrace-default-secret-key"
    AUTH_USERNAME: str = "admin"
    AUTH_PASSWORD: str = "admin"
    AUTH_TOKEN_EXPIRE_HOURS: int = 24

    # SMTP
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_USE_TLS: bool = True

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""

    # DingTalk
    DINGTALK_WEBHOOK_URL: str = ""
    DINGTALK_SECRET: str = ""

    # Slack
    SLACK_WEBHOOK_URL: str = ""

    # General
    LOG_LEVEL: str = "INFO"
    MAX_CONCURRENT_TASKS: int = 5
    NOTIFICATION_RETRY_MAX: int = 5
    NOTIFICATION_RATE_LIMIT_PER_MINUTE: int = 30

    @property
    def DB_PATH(self) -> str:
        return os.path.join(self.DATA_DIR, "changetrace.db")

    @property
    def DB_URL(self) -> str:
        return f"sqlite+aiosqlite:///{self.DB_PATH}"

    @property
    def SCREENSHOTS_DIR(self) -> str:
        return os.path.join(self.DATA_DIR, "screenshots")

    model_config = {"env_prefix": "CT_"}


settings = Settings()
