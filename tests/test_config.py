"""Unit tests for configuration defaults and settings."""

from pathlib import Path

from opendb_core.config import Settings


class TestSettingsDefaults:
    def test_default_backend(self):
        s = Settings()
        assert s.backend == "postgres"

    def test_default_max_file_size(self):
        s = Settings()
        assert s.max_file_size == 100 * 1024 * 1024  # 100 MB

    def test_default_port(self):
        s = Settings()
        assert s.port == 8000

    def test_default_host(self):
        s = Settings()
        assert s.host == "0.0.0.0"

    def test_default_ocr_enabled(self):
        s = Settings()
        assert s.ocr_enabled is True

    def test_default_ocr_languages(self):
        s = Settings()
        assert "eng" in s.ocr_languages

    def test_default_memory_decay(self):
        s = Settings()
        assert s.memory_decay_halflife_days == 30.0

    def test_default_auth_key_empty(self):
        s = Settings()
        assert s.auth_api_key == ""

    def test_default_watch_max(self):
        s = Settings()
        assert s.watch_max_watchers == 10

    def test_default_index_max_concurrent(self):
        s = Settings()
        assert s.index_max_concurrent == 4

    def test_default_cors_origins(self):
        s = Settings()
        assert "*" in s.cors_origins

    def test_file_storage_path_is_path(self):
        s = Settings()
        assert isinstance(s.file_storage_path, Path)

    def test_opendb_dir_is_path(self):
        s = Settings()
        assert isinstance(s.opendb_dir, Path)
