from pathlib import Path

from opendb_core.utils.hashing import compute_sha256


class TestComputeSha256:
    def test_known_digest(self, tmp_path: Path) -> None:
        """SHA-256 of known content matches expected hex digest."""
        f = tmp_path / "hello.txt"
        f.write_bytes(b"hello")
        # sha256("hello") is well-known
        assert compute_sha256(f) == (
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        )

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"content A")
        f2.write_bytes(b"content B")
        assert compute_sha256(f1) != compute_sha256(f2)

    def test_same_content_same_hash(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"identical")
        f2.write_bytes(b"identical")
        assert compute_sha256(f1) == compute_sha256(f2)

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        # sha256 of empty input is well-known
        assert compute_sha256(f) == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )
