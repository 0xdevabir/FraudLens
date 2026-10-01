"""Paths and runtime settings shared by every layer."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]

DEV_JWT_SECRET = "dev-only-change-me-never-use-this-key-outside-a-laptop"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FRAUDLENS_", env_file=".env", extra="ignore")

    data_dir: Path = BACKEND_DIR / "data"
    artifacts_dir: Path = BACKEND_DIR / "artifacts"
    dataset: str = "full"  # sub-directory of data_dir the platform serves

    # Model and policy the API serves. None means the promoted (CURRENT) model version.
    models_root: Path | None = None
    model_version: str | None = None
    policy_version: str = "v1"

    database_url: str = "postgresql+psycopg://fraudlens:fraudlens_dev@127.0.0.1:5433/fraudlens"
    redis_url: str = "redis://127.0.0.1:6380/0"
    events_stream: str = "fraudlens:events"
    alerts_channel: str = "fraudlens:alerts"
    run_worker: bool = True  # consume the event stream inside the API process

    # Signing key for access tokens. The default is for local development only;
    # the API refuses to start with it when environment == "production".
    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    jwt_ttl_minutes: int = 480
    environment: str = "development"
    login_max_attempts: int = 5
    login_window_seconds: int = 300

    # Password for the demo accounts created by `fraudlens.platform.seed`.
    # Unset: a random one is generated per account and printed once.
    seed_password: SecretStr | None = None

    # Case notes from the language model. Off unless enabled and ANTHROPIC_API_KEY is set.
    llm_notes: bool = False

    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @property
    def dataset_dir(self) -> Path:
        return self.data_dir / self.dataset

    @property
    def models_dir(self) -> Path:
        return self.models_root or self.artifacts_dir / "models"

    @property
    def production(self) -> bool:
        return self.environment == "production"


settings = Settings()
