<p align="center">
  <a href="https://github.com/wuwangzhang1216/museDB">
    <img loading="lazy" alt="MuseDB" src="https://github.com/wuwangzhang1216/museDB/raw/main/docs/assets/musedb-banner.svg" width="100%"/>
  </a>
</p>

# MuseDB

<p align="center">
  <strong>The AI-Native File Database</strong><br/>
  <code>cat</code> + <code>grep</code> for any file format. Parse once, query forever.
</p>

<p align="center">
  <a href="https://www.gnu.org/licenses/agpl-3.0"><img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="License: AGPL v3"/></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"/></a>
  <a href="https://pypi.org/project/musedb/"><img src="https://img.shields.io/pypi/v/musedb" alt="PyPI version"/></a>
  <a href="https://github.com/wuwangzhang1216/museDB/stargazers"><img src="https://img.shields.io/github/stars/wuwangzhang1216/museDB" alt="GitHub stars"/></a>
</p>

MuseDB turns any file — code, PDF, DOCX, PPTX, XLSX, CSV, images — into instantly-searchable plain text through HTTP endpoints and MCP tools. Built for LLM agents that need to read, search, and discover files without writing parsing scripts.

## Install

```bash
pip install musedb                # Core (REST API server, PostgreSQL)
pip install musedb[cli]           # + CLI + embedded SQLite mode (zero-config)
pip install musedb[mcp]           # + MCP server
pip install musedb[integration]   # + Python library client
```

## Quick Start

### Embedded mode (zero-config, no PostgreSQL needed)

```bash
pip install musedb[cli]
musedb index ./my_workspace       # parse & index everything
musedb serve-mcp                  # start MCP server over stdio
```

Or from Python:

```python
from musedb import MuseDB
db = MuseDB.open("./my_workspace")
await db.init()
await db.index()
text = await db.read("report.pdf", pages="1-3")
results = await db.search("quarterly revenue")
await db.close()
```

### Server mode (shared/team, PostgreSQL)

```bash
git clone https://github.com/wuwangzhang1216/museDB.git
cd museDB
docker-compose up -d
```

MuseDB is now running at `http://localhost:8000`.

## MCP Server

MuseDB ships with a built-in MCP (Model Context Protocol) server that exposes 3 tools — `musedb_read`, `musedb_search`, `musedb_glob` — designed to completely replace an agent's built-in `read`, `grep`, and `glob` tools.

### Install & Run

**Embedded mode** (zero-config, no PostgreSQL needed):

```bash
pip install musedb[cli]
musedb index ./my_workspace       # index your files
musedb serve-mcp                  # start MCP server (stdio)
```

Configure in your agent:

```yaml
mcp:
  musedb:
    transport: stdio
    command: musedb
    args: ["serve-mcp", "--workspace", "/path/to/workspace"]
```

**Server mode** (shared PostgreSQL backend):

```bash
pip install -e ".[mcp]"
python -m mcp_server              # stdio transport (default)
python -m mcp_server --transport streamable_http --port 8200  # HTTP transport
```

Configure in your agent:

```yaml
mcp:
  musedb:
    transport: stdio
    command: python
    args: ["-m", "mcp_server"]
    cwd: "/path/to/museDB"
    env:
      MUSEDB_URL: "http://localhost:8000"
```

### MCP Tools

#### `musedb_read` — Read any file

Reads code files with line numbers (cat -n style) and documents as plain text. Auto-detects file type.

```
musedb_read(filename="main.py")                          # Code with line numbers
musedb_read(filename="report.pdf", pages="1-3")          # PDF pages 1-3
musedb_read(filename="report.pdf", grep="revenue+growth") # Search within file
musedb_read(filename="budget.xlsx", format="json")        # Structured spreadsheet
musedb_read(filename="app.py", offset=50, limit=31)       # Lines 50-80
```

#### `musedb_search` — Search across code and documents

Unified search: regex grep for code files, full-text search for documents. Auto-detects mode.

```
musedb_search(query="def main", path="/workspace", glob="*.py")  # Grep code
musedb_search(query="quarterly revenue")                          # FTS documents
musedb_search(query="TODO", path="/src", case_insensitive=True)   # Case insensitive
musedb_search(query="import", path="/src", glob="*.ts", context=2) # With context
```

#### `musedb_glob` — Find files

File pattern matching, sorted by modification time (newest first).

```
musedb_glob(pattern="**/*.py", path="/workspace")
musedb_glob(pattern="src/**/*.{ts,tsx}", path="/workspace")
```

---

## Python Library

MuseDB can be used as a Python library — no HTTP server needed.

**Embedded mode** (SQLite, zero-config):

```bash
pip install musedb[cli]
```

```python
from musedb import MuseDB

db = MuseDB.open("./my_workspace")
await db.init()
await db.index()                              # index workspace root
text = await db.read("report.pdf", pages="1-3")
results = await db.search("quarterly revenue")
await db.close()
```

**Server mode** (PostgreSQL):

```bash
pip install musedb[integration]
```

```python
from musedb_integration import MuseDBClient, create_tools, ensure_indexed, index_file

# Embedded (SQLite, no PostgreSQL)
db = MuseDBClient(workspace_path="./my_workspace")
await db.init()

# Or server mode (PostgreSQL)
db = MuseDBClient("postgresql://musedb:musedb@localhost:5432/musedb")
await db.init()

# Read any file
text = await db.read_file("report.pdf", pages="1-3")
text = await db.read_file("main.py", numbered=True)           # Code with line numbers
data = await db.read_file("budget.xlsx", format="json")        # Structured JSON

# Search across all indexed documents
results = await db.search("quarterly revenue")                  # Full-text search
results = await db.search("def main", mode="grep", path="/workspace", glob="*.py")  # Regex grep

# Find files
files = await db.glob_files("**/*.py", path="/workspace")

# Index a directory (starts watching for changes)
await db.index_directory("/path/to/documents")

# Upload a single file
await db.upload_file("/path/to/report.pdf")

# Cleanup
await db.close()
```

### Integrate with your agent's tool system

```python
from musedb_integration import MuseDBClient, create_tools
from your_app.tool import ToolDefinition, ToolResult  # your base classes

db = MuseDBClient(workspace_path="./my_workspace")  # embedded mode
# or: db = MuseDBClient("postgresql://...")           # server mode
await db.init()

# Creates 3 tools (id="read", "grep", "glob") that replace built-in tools.
# Falls back to local file operations when MuseDB is unavailable.
for tool in create_tools(db, ToolDefinition, ToolResult):
    registry.register(tool)
```

---

## Agent Quick Start (REST API)

Give your agent these tools and it can read any file format:

### Index a directory

```bash
# Scan a folder, ingest all files, and start watching for changes
curl -X POST "http://localhost:8000/index?path=/Users/me/Documents"
```
```json
{
  "path": "/Users/me/Documents",
  "total_files": 42,
  "ingested": 38,
  "skipped": 2,
  "failed": 1,
  "unsupported": 1,
  "watch_id": "a3f2b1c9d0e4",
  "files": [
    {"filename": "report.pdf", "status": "ready", "id": "a1b2c3..."},
    {"filename": "data.xlsx", "status": "ready", "id": "d4e5f6..."}
  ]
}
```

After the initial scan, MuseDB **automatically watches** the directory — any new or modified files are ingested in real time.

### Upload a single file

```bash
curl -X POST http://localhost:8000/files -F "file=@report.pdf"
```
```json
{"id": "a1b2c3...", "filename": "report.pdf", "status": "ready", "total_pages": 12, "total_lines": 847}
```

### Read a file

```bash
# Read the whole file
curl http://localhost:8000/read/report.pdf

# Read with line numbers (code files)
curl "http://localhost:8000/read/main.py?numbered=true"

# Read pages 1-3 only
curl "http://localhost:8000/read/report.pdf?pages=1-3"

# Read lines 50-80
curl "http://localhost:8000/read/report.pdf?lines=50-80"

# Grep for a pattern
curl "http://localhost:8000/read/report.pdf?grep=revenue"

# Multi-term search (AND logic)
curl "http://localhost:8000/read/report.pdf?grep=revenue+growth"

# Read a specific Excel sheet
curl "http://localhost:8000/read/budget.xlsx?pages=Revenue"

# Get structured JSON from a spreadsheet
curl "http://localhost:8000/read/budget.xlsx?format=json"
```
```json
{
  "sheets": [
    {
      "name": "Revenue",
      "columns": ["Month", "Amount", "Category"],
      "rows": [["Jan", 50000, "Sales"], ["Feb", 62000, "Sales"]],
      "total_rows": 12
    }
  ]
}
```

### Search across all files

```bash
# Full-text search (documents)
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "quarterly revenue"}'

# Regex grep (code files)
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "def main", "mode": "grep", "path": "/workspace", "glob": "*.py"}'
```
```json
{
  "total": 3,
  "results": [
    {"filename": "report.pdf", "page_number": 4, "highlight": "...quarterly revenue grew 23%...", "relevance_score": 0.89},
    {"filename": "slides.pptx", "page_number": 2, "highlight": "...quarterly revenue target...", "relevance_score": 0.71}
  ]
}
```

### Find files (glob)

```bash
curl "http://localhost:8000/glob?pattern=**/*.py&path=/workspace"
```
```json
{
  "count": 42,
  "truncated": false,
  "files": ["src/main.py", "src/utils/helper.py", "tests/test_main.py"]
}
```

### List files with metadata

```bash
curl http://localhost:8000/files
```
```json
{
  "total": 42,
  "files": [
    {
      "id": "a1b2c3...",
      "filename": "Invoice_March.pdf",
      "mime_type": "application/pdf",
      "file_size": 245760,
      "status": "ready",
      "metadata": {
        "inferred_type": "invoice",
        "fs_created": 1710000000.0,
        "fs_modified": 1710100000.0,
        "_extracted": {"author": "Finance Team", "title": "March Invoice"}
      },
      "created_at": "2024-03-15T10:00:00",
      "updated_at": "2024-03-15T10:00:05"
    }
  ]
}
```

### Manage directory watchers

```bash
# List active watchers
curl http://localhost:8000/watch

# Check watcher stats
curl http://localhost:8000/watch/a3f2b1c9d0e4

# Stop watching a directory
curl -X DELETE http://localhost:8000/watch/a3f2b1c9d0e4
```

---

## Agent Tool Definitions

Copy-paste these tool definitions into your agent:

```python
MUSEDB = "http://localhost:8000"

def index_directory(path: str, tags: list[str] = None) -> dict:
    """Index a local directory. Ingests all supported files and starts watching for changes.

    Args:
        path: Absolute path to a local directory.
        tags: Optional tags to apply to all ingested files.
    """
    import httpx, json
    params = {"path": path}
    if tags:
        params["tags"] = json.dumps(tags)
    return httpx.post(f"{MUSEDB}/index", params=params).json()

def upload_file(filepath: str) -> dict:
    """Upload a file to MuseDB. Supports PDF, DOCX, PPTX, XLSX, CSV, TXT, images."""
    import httpx
    with open(filepath, "rb") as f:
        return httpx.post(f"{MUSEDB}/files", files={"file": f}).json()

def read_file(filename: str, pages: str = "", lines: str = "", grep: str = "", format: str = "", numbered: bool = False) -> str | dict:
    """Read a file as plain text (or structured JSON for spreadsheets).

    Args:
        filename: Exact filename, partial match, or file UUID.
        pages: Optional. Page range like "1-3", "5", or sheet name like "Revenue".
        lines: Optional. Line range like "50-80".
        grep: Optional. Search pattern. Use + for AND: "revenue+growth".
        format: Optional. "json" for structured spreadsheet data with columns and rows.
        numbered: Optional. True to include line numbers (cat -n style).
    """
    import httpx
    params = {k: v for k, v in {"pages": pages, "lines": lines, "grep": grep, "format": format}.items() if v}
    if numbered:
        params["numbered"] = "true"
    resp = httpx.get(f"{MUSEDB}/read/{filename}", params=params)
    if format == "json":
        return resp.json()
    return resp.text

def search(query: str, mode: str = "fts", limit: int = 10, path: str = "", glob: str = "", case_insensitive: bool = False, context: int = 0) -> dict:
    """Search across files. Full-text search for documents, regex grep for code.

    Args:
        query: Search query or regex pattern.
        mode: "fts" for document full-text search, "grep" for regex code search, "auto" to detect.
        limit: Max results to return.
        path: Directory to search in (grep mode).
        glob: File pattern filter e.g. "*.py" (grep mode).
        case_insensitive: Case insensitive search.
        context: Context lines before/after matches (grep mode).
    """
    import httpx
    body = {"query": query, "mode": mode, "limit": limit}
    if path:
        body["path"] = path
    if glob:
        body["glob"] = glob
    if case_insensitive:
        body["case_insensitive"] = True
    if context:
        body["context"] = context
    return httpx.post(f"{MUSEDB}/search", json=body).json()

def glob_files(pattern: str, path: str = "") -> dict:
    """Find files matching a glob pattern. Returns paths sorted by modification time.

    Args:
        pattern: Glob pattern e.g. "**/*.py", "src/**/*.ts".
        path: Root directory to search in.
    """
    import httpx
    params = {"pattern": pattern}
    if path:
        params["path"] = path
    return httpx.get(f"{MUSEDB}/glob", params=params).json()

def list_files(tags: str = "", mime_type: str = "", filename: str = "") -> dict:
    """List indexed files with metadata, document type, and timestamps.

    Args:
        tags: Optional. Filter by tag.
        mime_type: Optional. Filter by MIME type.
        filename: Optional. Fuzzy filename search.
    """
    import httpx
    params = {k: v for k, v in {"tags": tags, "mime_type": mime_type, "filename": filename}.items() if v}
    return httpx.get(f"{MUSEDB}/files", params=params).json()
```

### OpenAI Function Calling Format

```json
[
  {
    "name": "index_directory",
    "description": "Index a local directory. Recursively ingests all supported files and starts real-time watching for new/modified files.",
    "parameters": {
      "type": "object",
      "properties": {
        "path": {"type": "string", "description": "Absolute path to a local directory"},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to apply to all ingested files"}
      },
      "required": ["path"]
    }
  },
  {
    "name": "upload_file",
    "description": "Upload a document to MuseDB. Supports PDF, DOCX, PPTX, XLSX, CSV, TXT, and images.",
    "parameters": {
      "type": "object",
      "properties": {
        "filepath": {"type": "string", "description": "Path to the file to upload"}
      },
      "required": ["filepath"]
    }
  },
  {
    "name": "read_file",
    "description": "Read any file — code with line numbers, documents as plain text, spreadsheets as structured JSON.",
    "parameters": {
      "type": "object",
      "properties": {
        "filename": {"type": "string", "description": "Filename, partial match, or UUID"},
        "pages": {"type": "string", "description": "Page range: '1-3', '5', or sheet name like 'Revenue'"},
        "lines": {"type": "string", "description": "Line range: '50-80'"},
        "grep": {"type": "string", "description": "Search pattern. Use + for AND: 'revenue+growth'"},
        "format": {"type": "string", "enum": ["json"], "description": "Set to 'json' for structured spreadsheet output"},
        "numbered": {"type": "boolean", "description": "Add line numbers (cat -n style)"}
      },
      "required": ["filename"]
    }
  },
  {
    "name": "search",
    "description": "Search across code files (regex grep) and documents (full-text search). Auto-detects mode based on parameters.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {"type": "string", "description": "Search query or regex pattern"},
        "mode": {"type": "string", "enum": ["fts", "grep", "auto"], "description": "Search mode (default: fts)"},
        "path": {"type": "string", "description": "Directory to search in (grep mode)"},
        "glob": {"type": "string", "description": "File pattern filter e.g. '*.py' (grep mode)"},
        "case_insensitive": {"type": "boolean", "description": "Case insensitive search"},
        "context": {"type": "integer", "description": "Context lines before/after matches (grep mode)"},
        "limit": {"type": "integer", "description": "Max results (default 10)"}
      },
      "required": ["query"]
    }
  },
  {
    "name": "glob_files",
    "description": "Find files matching a glob pattern. Returns paths sorted by modification time (newest first).",
    "parameters": {
      "type": "object",
      "properties": {
        "pattern": {"type": "string", "description": "Glob pattern e.g. '**/*.py', 'src/**/*.ts'"},
        "path": {"type": "string", "description": "Root directory to search in"}
      },
      "required": ["pattern"]
    }
  },
  {
    "name": "list_files",
    "description": "List indexed files with metadata including document type inference, file sizes, and timestamps.",
    "parameters": {
      "type": "object",
      "properties": {
        "tags": {"type": "string", "description": "Filter by tag"},
        "mime_type": {"type": "string", "description": "Filter by MIME type"},
        "filename": {"type": "string", "description": "Fuzzy filename search"}
      }
    }
  }
]
```

---

## Why MuseDB?

Without MuseDB, agents write inline parsing scripts for every file:

```python
# Agent writes this for every PDF — 500+ tokens, often fails
run_command("""python -c "
import PyMuPDF; doc = PyMuPDF.open('report.pdf')
for page in doc: print(page.get_text())
" """)
```

With MuseDB:

```python
read_file("report.pdf")  # 50 tokens, always works
```

**Benchmarked across 4 LLMs on 24 document tasks (vs CMD agent):**

| Metric | Without MuseDB | With MuseDB |
|--------|---------------|-------------|
| Tokens used | 100% | **27-45%** (55-73% saved) |
| Task speed | 100% | **36-58%** faster |
| Answer quality | 2.4-3.2 / 5 | **3.4-3.9 / 5** |
| Success rate | 79% (19/24) | **100% (24/24)** |

**MuseDB FTS vs RAG vector retrieval (25-325 docs, scaled benchmark):**

| Scale | Docs | FTS Tokens | RAG Tokens | FTS Saves | FTS Quality | RAG Quality |
|-------|------|-----------|-----------|-----------|------------|------------|
| small | 25 | 83,543 | 157,589 | **47%** | 3.9/5 | 4.2/5 |
| medium | 125 | 76,460 | 136,543 | **44%** | **4.7/5** | 4.0/5 |
| large | 325 | 62,241 | 113,031 | **45%** | **4.6/5** | 3.5/5 |

FTS keyword search saves 44-47% tokens vs vector retrieval at all scales, and **answer quality improves with scale** while RAG degrades from distractor noise.

See [benchmark/REPORT.md](benchmark/REPORT.md) for full methodology.

---

## All Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/index` | `POST` | Index a directory and start watching (`?path=`, `?tags=`, `?max_concurrent=`) |
| `/files` | `POST` | Upload a single file (multipart form) |
| `/files` | `GET` | List files with metadata (`?tags=`, `?mime_type=`, `?filename=`, `?sort=`) |
| `/files/{id}` | `GET` | Get file details (metadata, inferred type, timestamps) |
| `/files/{id}` | `DELETE` | Delete a file |
| `/read/{filename}` | `GET` | Read as plain text (`?pages=`, `?lines=`, `?grep=`, `?toc=`, `?format=json`, `?numbered=true`) |
| `/search` | `POST` | Full-text search or regex grep (`{"query", "mode", "path", "glob", ...}`) |
| `/glob` | `GET` | Find files by glob pattern (`?pattern=`, `?path=`) |
| `/watch` | `GET` | List active directory watchers |
| `/watch/{id}` | `GET` | Get watcher details and stats |
| `/watch/{id}` | `DELETE` | Stop watching a directory |
| `/health` | `GET` | Health check |

## Key Features

- **Dual-mode runtime** — Embedded (SQLite, zero-config, `pip install musedb[cli]`) or Server (PostgreSQL, shared team access); same API either way
- **MCP Server** — 3 tools (`musedb_read`, `musedb_search`, `musedb_glob`) that replace an agent's built-in read/grep/glob
- **Code file support** — Read code with line numbers (`?numbered=true`), auto-detected by MCP tools
- **Regex grep** — `POST /search` with `mode=grep` for code search with regex, context lines, and file filtering
- **File discovery** — `GET /glob` for pathlib glob matching, sorted by modification time
- **Directory indexing** — `POST /index?path=` recursively scans and ingests all supported files
- **Real-time sync** — After indexing, directories are automatically watched for new/modified files via OS-native events (inotify/FSEvents/ReadDirectoryChangesW)
- **Structured spreadsheet output** — `?format=json` returns XLSX/CSV as `{sheets: [{columns, rows}]}` for direct analysis
- **Document type inference** — Automatically classifies files as invoice, receipt, contract, report, etc.
- **File metadata enrichment** — Filesystem timestamps, extracted document properties, inferred types
- **Duplicate detection** — SHA-256 checksum deduplication across uploads and directory scans
- **Full-text search** — PostgreSQL FTS for English, trigram fallback for CJK (Chinese/Japanese/Korean)
- **Fuzzy filename resolution** — Find files by exact name, partial match, or UUID

## Supported Formats

| Format | Extensions | Features |
|--------|-----------|----------|
| PDF | `.pdf` | Pages, tables, OCR for scanned docs, metadata |
| Word | `.docx` | Page breaks, tables, headings, metadata |
| PowerPoint | `.pptx` | Slides, speaker notes, tables, metadata |
| Excel | `.xlsx` | Multiple sheets, structured JSON output, metadata |
| CSV | `.csv` | Auto-encoding detection, structured JSON output |
| Text | `.txt` `.md` `.html` `.json` | Paragraph chunking, heading detection |
| Code | `.py` `.js` `.ts` `.go` `.rs` `.java` ... | Line-numbered output via `?numbered=true` |
| Images | `.png` `.jpg` `.tiff` `.bmp` | OCR via Tesseract (English + Chinese) |

## Configuration

Environment variables with `FILEDB_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `FILEDB_BACKEND` | `postgres` | Storage backend: `postgres` or `sqlite` |
| `FILEDB_MUSEDB_DIR` | `.musedb` | Embedded mode workspace directory |
| `FILEDB_DATABASE_URL` | `postgresql://musedb:musedb@localhost:5432/musedb` | PostgreSQL connection (server mode) |
| `FILEDB_FILE_STORAGE_PATH` | `./data` | File blob storage directory |
| `FILEDB_MAX_FILE_SIZE` | `104857600` | Max upload size (100MB) |
| `FILEDB_OCR_ENABLED` | `true` | Enable OCR for images |
| `FILEDB_OCR_LANGUAGES` | `eng+chi_sim+chi_tra` | Tesseract languages |
| `FILEDB_INDEX_MAX_CONCURRENT` | `4` | Default concurrent ingestion workers |
| `FILEDB_INDEX_EXCLUDE_PATTERNS` | `[]` | Additional directory exclude patterns |
| `FILEDB_WATCH_MAX_WATCHERS` | `10` | Max concurrent directory watchers |

MCP Server environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `MUSEDB_URL` | `http://localhost:8000` | MuseDB REST API URL |

## Manual Setup

**Embedded mode:**

```bash
pip install musedb[cli]
musedb init ./workspace
musedb index ./workspace
musedb serve-mcp             # MCP over stdio
musedb serve                 # HTTP API at http://127.0.0.1:8000
```

**Server mode (PostgreSQL):**

```bash
pip install -e .
createdb musedb && psql musedb < sql/schema.sql
uvicorn app.main:app --reload
```

Requires Python 3.11+. Server mode additionally requires PostgreSQL 16+. Tesseract OCR is optional (for image/scanned PDF support).

### MCP Server Setup

```bash
pip install -e ".[mcp]"
python -m mcp_server
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

[AGPL-3.0](LICENSE) — Source code must be shared when running as a network service.
