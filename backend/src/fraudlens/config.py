"""Paths and runtime settings shared by every layer."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from .secret_sources import SecretFilesSource

BACKEND_DIR = Path(__file__).resolve().parents[2]

DEV_JWT_SECRET = "dev-only-change-me-never-use-this-key-outside-a-laptop"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FRAUDLENS_", env_file=".env", extra="ignore")

    data_dir: Path = BACKEND_DIR / "data"
    artifacts_dir: Path = BACKEND_DIR / "artifacts"
    dataset: str = "full"  # sub-directory of data_dir the platform serves
    # Simulator profile for `fraudlens.simulator.generate`: "default" or "calibrated"
    # (Bangladesh-sourced parameters, docs/DATA_ASSUMPTIONS.md §11).
    sim_profile: str = "default"

    # Model and policy the API serves. None means the promoted (CURRENT) model version.
    models_root: Path | None = None
    model_version: str | None = None
    policy_version: str = "v2"
    # A challenger scored next to the served model on every decision. It is recorded
    # for comparison and never decides anything.
    shadow_model_version: str | None = None

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
    # One-click sign-in as a demo account, with no password. For a demo on a machine
    # only you can reach; ignored in production, where the API also refuses to start.
    demo_login: bool = False

    # Case notes from the language model. Off unless enabled and ANTHROPIC_API_KEY is set.
    llm_notes: bool = False

    # Where the console is served from in development (`make console`).
    cors_origins: list[str] = ["http://localhost:3100", "http://127.0.0.1:3100"]

    # Signing keys with ids, re-read when the file changes (`fraudlens.platform.keys`).
    # When set they replace jwt_secret: the current key signs, unretired ones verify.
    jwt_keyring: Path | None = None
    # Partner HMAC keys for /v1/ingest (docs/INGEST.md); without them it answers 404.
    ingest_keyring: Path | None = None
    ingest_window_seconds: int = 300  # how far a request's timestamp may be from ours
    # Reverse proxies (CIDRs) whose X-Forwarded-For is believed; nobody else's is.
    trusted_proxies: list[str] = []

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # FRAUDLENS_<NAME>_FILE and FRAUDLENS_SECRETS_PROVIDER: see secret_sources.py.
        files = SecretFilesSource(settings_cls)
        return init_settings, env_settings, files, dotenv_settings, file_secret_settings

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
