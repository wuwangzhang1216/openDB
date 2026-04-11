# Contributing to OpenDB

Thanks for your interest in contributing to OpenDB! This guide will help you get started.

## Getting Started

1. Fork the repository and clone it locally:

```bash
git clone https://github.com/wuwangzhang1216/openDB.git
cd openDB
```

2. Install in development mode:

```bash
pip install -e ".[dev]"
```

3. Run the tests to make sure everything works:

```bash
pytest
```

## Development Workflow

1. Create a new branch from `main`:

```bash
git checkout -b feature/your-feature-name
```

2. Make your changes and add tests if applicable.

3. Run tests before committing:

```bash
pytest
```

4. Add an entry to [`CHANGELOG.md`](CHANGELOG.md) under the `## [Unreleased]` section if your change is user-visible (new feature, API change, bug fix, breaking change). Use the `Added` / `Changed` / `Fixed` / `Removed` / `Deprecated` / `Security` categories from [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Internal refactors and docs-only tweaks don't need an entry.

5. Commit your changes with a clear message:

```bash
git commit -m "Add: brief description of the change"
```

6. Push to your fork and open a Pull Request.

## Project Structure

```
opendb_core/          # Core library — parsers, services, storage, routers
  parsers/            # File format parsers (PDF, DOCX, PPTX, XLSX, etc.)
  services/           # Business logic (search, index, memory, etc.)
  storage/            # Database backends (PostgreSQL, SQLite)
  routers/            # FastAPI route handlers
mcp_server/           # MCP server for agent integration
opendb/               # CLI entry point
opendb_integration/   # Python client SDK
tests/                # Test suite
benchmark/            # Performance benchmarks
```

## What to Contribute

- **Bug fixes** — Check [open issues](https://github.com/wuwangzhang1216/openDB/issues)
- **New file parsers** — Add support for more document formats
- **Performance improvements** — Faster parsing, better search ranking
- **Documentation** — Fix typos, improve examples, add guides
- **Tests** — Increase test coverage

## Code Guidelines

- Python 3.11+
- Use `async/await` for I/O operations
- Follow existing code style — no need for a linter config, just match what's there
- Keep PRs focused — one feature or fix per PR
- Add tests for new functionality
- Update `CHANGELOG.md` under `## [Unreleased]` for any user-visible change

## Reporting Issues

When opening an issue, please include:

- What you expected to happen
- What actually happened
- Steps to reproduce
- Python version and OS

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
