"""Pydantic input models for MuseDB MCP tools."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ReadInput(BaseModel):
    """Input for reading any file — code with line numbers, documents as plain text."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    filename: str = Field(
        ..., description="File path, filename, partial match, or UUID", min_length=1
    )
    offset: int | None = Field(
        None, description="Start line number (1-based)", ge=1
    )
    limit: int | None = Field(
        None, description="Max lines to return", ge=1
    )
    pages: str | None = Field(
        None, description="Page range '1-3', page number '5', or sheet name 'Revenue'"
    )
    grep: str | None = Field(
        None, description="Search within file. Use + for AND: 'revenue+growth'"
    )
    format: str | None = Field(
        None, description="Set to 'json' for structured spreadsheet output with columns and rows"
    )


class SearchInput(BaseModel):
    """Input for searching across code files (regex) and documents (full-text)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ..., description="Search query or regex pattern", min_length=1
    )
    mode: str = Field(
        "auto",
        description="'grep' for regex code search, 'fts' for document full-text search, 'auto' to detect",
    )
    path: str | None = Field(
        None, description="Directory to search in (grep mode)"
    )
    glob: str | None = Field(
        None, description="File pattern filter e.g. '*.py', '*.{ts,tsx}' (grep mode)"
    )
    case_insensitive: bool = Field(
        False, description="Case insensitive search"
    )
    context: int = Field(
        0, description="Context lines before/after each match (grep mode)", ge=0, le=10
    )
    limit: int = Field(20, description="Max results", ge=1, le=100)
    offset: int = Field(0, description="Pagination offset", ge=0)


class GlobInput(BaseModel):
    """Input for finding files matching a glob pattern."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    pattern: str = Field(
        ..., description="Glob pattern e.g. '**/*.py', 'src/**/*.ts'", min_length=1
    )
    path: str | None = Field(
        None, description="Root directory to search in"
    )
