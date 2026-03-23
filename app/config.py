from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_env: str = "development"
    allowed_origins: str = "http://localhost:3000"

    # Database
    database_url: str = "postgresql+asyncpg://letta:letta@localhost/letta"

    # JWT
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    # Africa's Talking (SMS OTP)
    at_api_key: str = ""
    at_username: str = ""
    at_sender_id: str = ""

    # Resend (email)
    resend_api_key: str = ""
    resend_from_email: str = "noreply@yourdomain.com"

    # DigitalOcean Spaces
    do_spaces_key: str = ""
    do_spaces_secret: str = ""
    do_spaces_region: str = "nyc3"
    do_spaces_bucket: str = "letta-media"
    do_spaces_endpoint: str = "https://nyc3.digitaloceanspaces.com"

    # FCM v1
    fcm_service_account_json: str = ""
    fcm_project_id: str = ""

    # Message encryption at rest
    message_encryption_key: str = ""

    # Message retention
    delivered_message_ttl_days: int = 7    # delete delivered messages after N days
    undelivered_message_ttl_days: int = 30 # delete undelivered messages after N days

    # Dashboard
    admin_api_key: str = ""

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]


settings = Settings()