from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://musedb:musedb@localhost:5432/musedb"
    db_pool_min: int = 5
    db_pool_max: int = 20

    # File storage
    file_storage_path: Path = Path("./data")
    max_file_size: int = 100 * 1024 * 1024  # 100 MB
    sync_processing_limit: int = 50 * 1024 * 1024  # 50 MB

    # OCR
    ocr_enabled: bool = True
    ocr_languages: str = "eng+chi_sim+chi_tra"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]

    model_config = {"env_prefix": "FILEDB_", "env_file": ".env"}


settings = Settings()
