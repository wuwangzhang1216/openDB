from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://opendb:opendb@localhost:5432/opendb"
    db_pool_min: int = 5
    db_pool_max: int = 20

    # File storage
    file_storage_path: Path = Path("./data")
    max_file_size: int = 100 * 1024 * 1024  # 100 MB

    # OCR
    ocr_enabled: bool = True
    ocr_languages: str = "eng+chi_sim+chi_tra"

    # Vision (LLM-based image description — replaces Tesseract for images)
    vision_enabled: bool = True
    vision_api_key: str = ""  # OpenRouter API key; falls back to env OPENROUTER_API_KEY

    # Directory indexing
    index_max_concurrent: int = 4
    index_exclude_patterns: list[str] = []

    # Directory watching
    watch_max_watchers: int = 10

    # Agent memory
    memory_decay_halflife_days: float = 30.0  # time-decay half-life for recall scoring
    memory_stability_days: float = 30.0  # FSRS stability: days for confidence to drop to 0.9
    memory_confidence_threshold: float = 0.3  # below this, memory fades out of recall

    # Evaluation capture (opt-in): records real search/recall traffic for offline analysis
    eval_capture_enabled: bool = False

    # Lightweight link graph. Links are indexed deterministically; backlink
    # search boosting is opt-in so default ranking stays unchanged.
    link_extraction_enabled: bool = True
    backlink_boost_enabled: bool = False
    backlink_boost_weight: float = 0.05

    # Authentication (optional — if set, all requests require X-API-Key header)
    auth_api_key: str = ""

    # Storage backend: "postgres" (default, requires PostgreSQL) or "sqlite" (embedded, zero-config)
    backend: str = "postgres"

    # SQLite embedded mode — path to the .opendb directory
    opendb_dir: Path = Path(".opendb")

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]

    model_config = {"env_prefix": "FILEDB_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
