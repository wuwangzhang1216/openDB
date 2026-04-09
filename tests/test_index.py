"""Unit tests for directory indexing (Gap 1)."""

from pathlib import Path

import pytest

from opendb_core.services.index_service import _is_excluded, scan_directory


class TestScanDirectory:
    """Test directory scanning and exclusion logic."""

    def test_finds_all_files(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.csv").write_text("x,y\n1,2")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.md").write_text("# Title")

        result = scan_directory(tmp_path)
        names = {f.name for f in result}
        assert names == {"a.txt", "b.csv", "c.md"}

    def test_empty_directory(self, tmp_path: Path):
        result = scan_directory(tmp_path)
        assert result == []

    def test_excludes_git_directory(self, tmp_path: Path):
        (tmp_path / "ok.txt").write_text("good")
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("git stuff")

        result = scan_directory(tmp_path)
        names = {f.name for f in result}
        assert "ok.txt" in names
        assert "config" not in names

    def test_excludes_node_modules(self, tmp_path: Path):
        (tmp_path / "index.js").write_text("//js")
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("//pkg")

        result = scan_directory(tmp_path)
        assert len(result) == 1
        assert result[0].name == "index.js"

    def test_excludes_dotfiles(self, tmp_path: Path):
        (tmp_path / "visible.txt").write_text("ok")
        (tmp_path / ".hidden").write_text("secret")

        result = scan_directory(tmp_path)
        names = {f.name for f in result}
        assert "visible.txt" in names
        assert ".hidden" not in names

    def test_excludes_ds_store(self, tmp_path: Path):
        (tmp_path / "file.txt").write_text("data")
        (tmp_path / ".DS_Store").write_text("mac stuff")
        (tmp_path / "thumbs.db").write_text("win stuff")

        result = scan_directory(tmp_path)
        names = {f.name for f in result}
        assert names == {"file.txt"}

    def test_extra_excludes(self, tmp_path: Path):
        (tmp_path / "keep.txt").write_text("yes")
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "output.bin").write_text("binary")

        result = scan_directory(tmp_path, extra_excludes=["build"])
        names = {f.name for f in result}
        assert "keep.txt" in names
        assert "output.bin" not in names

    def test_skips_directories_themselves(self, tmp_path: Path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "file.txt").write_text("content")

        result = scan_directory(tmp_path)
        # Only files, not directories
        assert all(f.is_file() for f in result)
        assert len(result) == 1


class TestIsExcluded:
    """Test the _is_excluded helper."""

    def test_git_excluded(self):
        assert _is_excluded(Path(".git/config"), []) is True

    def test_pycache_excluded(self):
        assert _is_excluded(Path("__pycache__/mod.pyc"), []) is True

    def test_normal_file_not_excluded(self):
        assert _is_excluded(Path("src/main.py"), []) is False

    def test_dotfile_excluded(self):
        assert _is_excluded(Path(".env"), []) is True

    def test_ds_store_excluded(self):
        assert _is_excluded(Path("some/dir/.DS_Store"), []) is True

    def test_extra_pattern(self):
        assert _is_excluded(Path("dist/bundle.js"), ["dist"]) is True
