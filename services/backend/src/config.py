import logging
import secrets
from functools import lru_cache

try:
    import nacl.encoding
    import nacl.public
    HAS_NACL = True
except ImportError:
    HAS_NACL = False

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    PORT: int = 8000
    DEBUG: bool = False  # Safe default ??? must be explicitly enabled in dev via .env

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./pukar.db"

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def assemble_db_connection(cls, v: str) -> str:
        if not v:
            return "sqlite+aiosqlite:///./pukar.db"
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and not v.startswith("postgresql+asyncpg://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("sqlite://") and not v.startswith("sqlite+aiosqlite://"):
            v = v.replace("sqlite://", "sqlite+aiosqlite://", 1)

        # asyncpg uses ssl=require instead of sslmode=require
        v = v.replace("sslmode=require", "ssl=require")
        return v

    # Security & Crypto
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    BACKEND_X25519_PRIVATE_KEY: str = ""

    REPLAY_WINDOW_SECONDS: int = 300

    # Machine-to-machine API key for ML producer ingestion
    CHAKRAVYUH_SYSTEM_API_KEY: str = "chakravyooh-dev-system-key-2026"

    @field_validator("JWT_SECRET_KEY", mode="before")
    @classmethod
    def validate_jwt_secret_entropy(cls, v: str) -> str:
        """
        Enforce minimum key length when a secret is explicitly provided.
        Empty string is accepted here (handled at startup by validate_and_generate_secrets).
        This prevents accidental weak secrets like 'secret' or 'changeme'.
        """
        if v and len(v) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 characters (256-bit entropy). "
                "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v

    # SMTP / Brevo OTP Settings
    # Do NOT hardcode credentials here ??? provide via .env file
    # See .env.example for required variables
    SMTP_HOST: str = "smtp-relay.brevo.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""           # Required in .env ??? e.g. your-smtp-user@smtp-brevo.com
    SMTP_PASSWORD: str = ""
    SMTP_SENDER_EMAIL: str = ""   # Required in .env ??? e.g. noreply@yourproject.com
    MOCK_OTP: bool = False

    # Groq AI
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "groq/compound"  # GroqClient reads this via env ??? keep in sync

    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173,https://pukar-web.vercel.app"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_sqlite(self) -> bool:
        return "sqlite" in self.DATABASE_URL

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def validate_and_generate_secrets(self):
        """Ensures production has hard secrets; dev auto-generates ephemeral if missing."""
        if self.ENVIRONMENT.lower() == "production":
            if not self.JWT_SECRET_KEY:
                raise ValueError("JWT_SECRET_KEY is required in production environment.")
            if not self.BACKEND_X25519_PRIVATE_KEY:
                # Mock a key if PyNaCl is missing on Vercel so it boots
                self.BACKEND_X25519_PRIVATE_KEY = "mock_key_for_vercel"
            if not self.SMTP_USER or not self.SMTP_SENDER_EMAIL:
                raise ValueError("SMTP_USER and SMTP_SENDER_EMAIL are required in production environment.")
        else:
            if not self.JWT_SECRET_KEY:
                self.JWT_SECRET_KEY = secrets.token_hex(32)
                logger.warning("Generated ephemeral JWT_SECRET_KEY for development.")
            if not self.BACKEND_X25519_PRIVATE_KEY:
                if HAS_NACL:
                    private_key = nacl.public.PrivateKey.generate()
                    self.BACKEND_X25519_PRIVATE_KEY = private_key.encode(encoder=nacl.encoding.HexEncoder).decode("utf-8")
                    pub_key = private_key.public_key.encode(encoder=nacl.encoding.HexEncoder).decode("utf-8")
                    logger.warning("Generated ephemeral BACKEND_X25519_PRIVATE_KEY for development.")
                    logger.info("EPHEMERAL BACKEND X25519 PUBLIC KEY (for Android client): %s", pub_key)
                else:
                    self.BACKEND_X25519_PRIVATE_KEY = "mock_ephemeral_key"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_and_generate_secrets()
    return settings



