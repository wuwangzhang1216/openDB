from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class Page:
    page_number: int  # 1-indexed
    section_title: str | None
    text: str
    content_type: str = "text"  # 'text' | 'table' | 'note'


@dataclass
class ParseResult:
    pages: list[Page]
    extracted_metadata: dict = field(default_factory=dict)


class FileParser(Protocol):
    def parse(self, file_path: Path) -> ParseResult: ...
