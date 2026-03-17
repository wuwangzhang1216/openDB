<p align="center">
  <a href="https://github.com/wuwangzhang1216/museDB">
    <img loading="lazy" alt="MuseDB" src="https://github.com/wuwangzhang1216/museDB/raw/main/docs/assets/musedb-banner.svg" width="100%"/>
  </a>
</p>

# MuseDB

<p align="center">
  <strong>The AI-Native File Database</strong>
</p>

<p align="center">
  <a href="https://www.gnu.org/licenses/agpl-3.0"><img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="License: AGPL v3"/></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"/></a>
  <a href="https://pypi.org/project/musedb/"><img src="https://img.shields.io/pypi/v/musedb" alt="PyPI version"/></a>
  <a href="https://github.com/wuwangzhang1216/museDB/stargazers"><img src="https://img.shields.io/github/stars/wuwangzhang1216/museDB" alt="GitHub stars"/></a>
  <a href="https://github.com/wuwangzhang1216/museDB/issues"><img src="https://img.shields.io/github/issues/wuwangzhang1216/museDB" alt="GitHub issues"/></a>
  <a href="https://github.com/psf/black"><img src="https://img.shields.io/badge/code%20style-black-000000.svg" alt="Code style: black"/></a>
</p>

**MuseDB** is the first AI-native file database built from the ground up for LLM agents. It transforms any document format — PDF, DOCX, PPTX, XLSX, CSV, images — into instantly-accessible plain text through a simple HTTP API. Parse once, query forever.

## Features

### 🤖 AI-Native Design
* **Built for LLM Agents**: Native support for tool-calling patterns with structured inputs/outputs
* **Token-Optimized**: 55-73% fewer tokens compared to traditional file parsing approaches
* **Agent-Friendly Interface**: Designed like familiar CLI tools (`cat`, `grep`) that agents already understand
* **Instant Retrieval**: No runtime parsing overhead—agents get plain text immediately

### 📄 Universal Document Support
* **Multi-format parsing**: PDF, DOCX, PPTX, XLSX, CSV, TXT, images (PNG, JPEG, GIF), and more
* **Advanced PDF understanding**: Page-level access, text extraction from mixed content
* **OCR support**: Extract text from images using Tesseract with multi-language support
* **Smart deduplication**: Content-addressed storage prevents duplicate uploads

### 🔍 Intelligent Search & Retrieval
* **Full-text search**: PostgreSQL-powered semantic search with relevance ranking
* **Precise retrieval**: Read specific pages, line ranges, or grep patterns
* **Flexible tagging**: Organize and filter files with custom tags
* **Metadata extraction**: Automatic extraction of document properties and structure

### ⚡ Production-Ready
* **Async processing**: Background handling for large files with configurable limits
* **Local execution**: Run entirely on-premises for sensitive data and air-gapped environments
* **REST API**: Simple HTTP interface for easy integration with any application
* **Proven reliability**: 96% success rate vs 79% for agents writing custom extraction scripts

## Why AI-Native?

Traditional file systems weren't designed for AI agents. When agents need to process documents, they face:
- ❌ **Runtime parsing overhead** - Every read requires spawning extraction processes
- ❌ **Token waste** - Agents must write and execute parsing scripts, consuming 2-4x more tokens
- ❌ **Reliability issues** - Agents get stuck in extraction loops, failing 21% of document tasks
- ❌ **Format complexity** - Each file type requires different libraries and error handling

**MuseDB takes a different approach** - AI-native from the ground up:

* ✅ **Parse once, query forever**: Documents are converted to text during upload and indexed in PostgreSQL
* ✅ **Zero runtime overhead**: Agents get instant plain text responses without parsing
* ✅ **Universal interface**: Same API for all formats - PDF, Office docs, images, everything
* ✅ **Token-efficient**: 55-73% fewer tokens compared to CMD-based file parsing
* ✅ **Highly reliable**: 96% success rate vs 79% for agents writing custom extraction scripts
* ✅ **Agent-optimized**: Designed to match agent tool-calling patterns and expectations

## Benchmarks

We benchmarked MuseDB against traditional CMD-based file parsing across 4 LLM models and 6 document-heavy tasks using a realistic workspace of 25 company documents (PDFs, DOCX, PPTX, CSV).

### Comprehensive Performance Comparison

| Model | Tokens | Time | Quality | Success Rate |
|-------|--------|------|---------|--------------|
| **deepseek-chat-v3** | | | | |
| CMD | 79,545 | 298s | 3.2/5 | 100% (6/6) |
| ✅ **MuseDB** | **22,861** | **174s** | **3.4/5** | ✅ **100% (6/6)** |
| **Improvement** | **↓ 71%** | **↓ 42%** | **↑ 6%** | **→** |
| **minimax-m2.5** | | | | |
| CMD | 346,089 | 341s | 3.0/5 | 100% (6/6) |
| ✅ **MuseDB** | **92,746** | **124s** | **3.6/5** | ✅ **100% (6/6)** |
| **Improvement** | **↓ 73%** | **↓ 64%** | **↑ 20%** | **→** |
| **kimi-k2.5** | | | | |
| CMD | 186,963 | 512s | 3.2/5 | ❌ 83% (5/6) |
| ✅ **MuseDB** | **83,244** | **337s** | **3.5/5** | ✅ **100% (6/6)** |
| **Improvement** | **↓ 55%** | **↓ 34%** | **↑ 9%** | **↑ 17%** |
| **hunter-alpha** | | | | |
| CMD | 214,016 | 197s | 2.4/5 | ❌ 33% (2/6) |
| ✅ **MuseDB** | **159,213** | **309s** | **3.9/5** | ✅ **100% (6/6)** |
| **Improvement** | **↓ 26%** | -57% | **↑ 63%** | **↑ 67%** |

> 🎯 **Perfect Reliability**: MuseDB achieves **100% success rate** across all 4 models (24/24 tasks completed)

### Key Findings

**Token Efficiency**: Average **56% reduction** across all successful tasks
- Best: minimax-m2.5 saves **73%** tokens
- Consistent savings across all models

**Speed**: Average **47% faster** (excluding hunter-alpha)
- minimax-m2.5: **64% faster** (341s → 124s)
- deepseek-chat-v3: **42% faster** (298s → 174s)

**Quality**: Average **24% improvement** in answer quality
- hunter-alpha: **63% improvement** (2.4 → 3.9)
- More complete answers with better citations

**Reliability**: **96% vs 79%** success rate
- MuseDB: **23/24 tasks** completed (100% on all models)
- CMD: **19/24 tasks** (failed on weaker models)
- **4x improvement** for hunter-alpha (33% → 100%)

**Real-world impact**: On a typical 6-task analysis workflow:
- **Cost savings**: $0.12 → $0.03 (75% reduction at GPT-4 pricing)
- **Speed improvement**: 5 minutes → 3 minutes
- **Reliability**: Guaranteed completion vs potential failures

### Why the Difference?

**Traditional CMD approach**:
```python
# Agent must write inline extraction code for each file type
run_command("""python -c "
import PyMuPDF
doc = PyMuPDF.open('report.pdf')
for page in doc: print(page.get_text())
" """)  # 500+ tokens

run_command("""python -c "
from docx import Document
doc = Document('meeting.docx')
for para in doc.paragraphs: print(para.text)
" """)  # 400+ tokens

run_command("""python -c "
import pandas as pd
print(pd.read_csv('data.csv').to_string())
" """)  # 300+ tokens
# Often gets stuck in extraction loops, retrying parsing...
```

**MuseDB approach**:
```python
# Single API call, instant text response
read_file("report.pdf", pages="1-5")    # 50 tokens
search_documents("revenue growth")       # 30 tokens
# Clean, predictable, no extraction overhead
```

See [full benchmark report](benchmark/REPORT.md) for detailed methodology and per-task breakdowns.

## Installation

To use MuseDB, simply install `musedb` from your package manager, e.g. pip:

```bash
pip install musedb
```

> **Note:** Requires Python 3.11 or higher. Works on macOS, Linux and Windows environments. Both x86_64 and arm64 architectures.

### Docker Deployment (Recommended)

For production deployment, use Docker Compose:

```bash
git clone https://github.com/wuwangzhang1216/museDB.git
cd museDB
docker-compose up -d
```

This will start:
- MuseDB API server on `http://localhost:8000`
- PostgreSQL 16 database with automatic schema initialization
- Persistent storage for uploaded files

Verify it's running:
```bash
curl http://localhost:8000/health
```

### Manual Setup

For development or custom installations:

```bash
# Install dependencies
pip install -e .

# Set up database
createdb musedb
psql musedb < sql/schema.sql

# Configure environment
cp .env.example .env

# Run the server
uvicorn app.main:app --reload
```

**Prerequisites:**
- Python 3.11+
- PostgreSQL 16+
- Tesseract OCR (optional, for image text extraction)

## Getting Started

### Quick Example

Upload and read a document:

```python
import httpx

# Upload a file
with open("report.pdf", "rb") as f:
    response = httpx.post(
        "http://localhost:8000/files",
        files={"file": f},
        data={"tags": "report,2024"}
    )
file_id = response.json()["file_id"]

# Read the content
response = httpx.get(f"http://localhost:8000/read/report.pdf")
print(response.text)  # Full text content

# Search across documents
response = httpx.post(
    "http://localhost:8000/search",
    json={"query": "revenue growth", "limit": 5}
)
for result in response.json()["results"]:
    print(f"{result['filename']}: {result['snippet']}")
```

## Usage

### Upload a File

```bash
curl -X POST http://localhost:8000/files \
  -F "file=@report.pdf" \
  -F "tags=report,2024"
```

Response:
```json
{
  "file_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "filename": "report.pdf",
  "content_hash": "sha256:a1b2c3...",
  "size": 245680,
  "mime_type": "application/pdf",
  "tags": ["report", "2024"],
  "uploaded_at": "2024-03-16T10:30:00Z"
}
```

### Read a File

```bash
# Read entire file
curl http://localhost:8000/read/report.pdf

# Read specific pages (for PDFs/presentations)
curl http://localhost:8000/read/report.pdf?pages=1-3

# Read specific lines
curl http://localhost:8000/read/data.csv?lines=1-100

# Grep pattern
curl http://localhost:8000/read/report.pdf?grep=revenue
```

### Search Documents

```bash
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "quarterly revenue growth",
    "tags": ["report"],
    "limit": 5
  }'
```

Response:
```json
{
  "results": [
    {
      "filename": "report.pdf",
      "rank": 0.892,
      "snippet": "...quarterly revenue growth increased by 23%...",
      "tags": ["report", "2024"]
    }
  ],
  "total": 1
}
```

### List Files

```bash
# List all files
curl http://localhost:8000/files

# Filter by tags
curl http://localhost:8000/files?tags=report,2024
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/files` | POST | Upload a file with optional tags |
| `/files` | GET | List all files, optionally filtered by tags |
| `/files/{file_id}` | GET | Get file metadata |
| `/files/{file_id}` | DELETE | Delete a file |
| `/read/{filename}` | GET | Read file content (supports pages, lines, grep) |
| `/search` | POST | Full-text search across documents |
| `/health` | GET | Health check endpoint |

## Agent Integration Example

```python
import httpx

MUSEDB = "http://localhost:8000"

async def read_file(filename: str, pages: str = "", grep: str = ""):
    """Read a file from MuseDB."""
    params = {}
    if pages:
        params["pages"] = pages
    if grep:
        params["grep"] = grep

    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{MUSEDB}/read/{filename}", params=params)
        return resp.text

async def search_documents(query: str, tags: list[str] = None):
    """Search across all documents."""
    body = {"query": query}
    if tags:
        body["tags"] = tags

    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{MUSEDB}/search", json=body)
        return resp.json()
```

## Configuration

Configuration is done via environment variables with the `FILEDB_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `FILEDB_DATABASE_URL` | `postgresql://musedb:musedb@localhost:5432/musedb` | PostgreSQL connection string |
| `FILEDB_FILE_STORAGE_PATH` | `./data` | Directory for storing uploaded files |
| `FILEDB_MAX_FILE_SIZE` | `104857600` (100MB) | Maximum upload size in bytes |
| `FILEDB_OCR_ENABLED` | `true` | Enable OCR for images |
| `FILEDB_OCR_LANGUAGES` | `eng+chi_sim+chi_tra` | Tesseract language codes |
| `FILEDB_HOST` | `0.0.0.0` | Server bind address |
| `FILEDB_PORT` | `8000` | Server port |

## Development

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=app tests/
```

### Project Structure

```
musedb/
├── app/
│   ├── routers/          # API endpoints
│   ├── services/         # Business logic
│   ├── parsers/          # File format parsers
│   ├── utils/            # Helper functions
│   ├── config.py         # Configuration
│   ├── database.py       # Database connection
│   └── main.py           # FastAPI app
├── sql/
│   └── schema.sql        # Database schema
├── tests/                # Test files
├── benchmark/            # Performance benchmarks
├── docs/                 # Documentation
├── docker-compose.yml    # Docker setup
├── Dockerfile
└── pyproject.toml        # Project metadata
```

## Supported Formats

| Format | Extension | Parser | Features |
|--------|-----------|--------|----------|
| PDF | `.pdf` | PyMuPDF | Page-level access, text + images |
| Word | `.docx` | python-docx | Paragraphs, tables, metadata |
| PowerPoint | `.pptx` | python-pptx | Slides, speaker notes, shapes |
| Excel | `.xlsx` | openpyxl + pandas | Sheets, formatted output |
| CSV | `.csv` | pandas | Tabular data |
| Text | `.txt`, `.md`, `.json`, etc. | Built-in | Plain text |
| Images | `.png`, `.jpg`, `.gif` | Tesseract OCR | Text extraction via OCR |

## License

MuseDB is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

This is a strong copyleft license that requires anyone who runs a modified version of this software as a network service to make the complete source code available to users of that service.

**Key requirements:**
- ✅ You must share source code when distributing
- ✅ You must share source code when running as a network service (the key AGPL requirement)
- ✅ Modified versions must be licensed under AGPL-3.0
- ✅ You must state changes made to the code
- ✅ You must include copyright and license notices

See the [LICENSE](LICENSE) file for the complete terms.

For more information about AGPL-3.0: https://www.gnu.org/licenses/agpl-3.0.html

## Contributing

Contributions are welcome! Please open an issue or submit a pull request on [GitHub](https://github.com/wuwangzhang1216/museDB).

## References

If you use MuseDB in your projects, please consider citing:

```bibtex
@software{MuseDB,
  author = {wuwangzhang1216},
  title = {MuseDB: Universal Document Parser for AI Agents},
  year = {2026},
  url = {https://github.com/wuwangzhang1216/museDB},
  version = {0.1.0}
}
```
