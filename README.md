<p align="center">
  <a href="https://github.com/wuwangzhang1216/museDB">
    <img loading="lazy" alt="MuseDB" src="https://github.com/wuwangzhang1216/museDB/raw/main/docs/assets/musedb-banner.svg" width="100%"/>
  </a>
</p>

<p align="center">
  <strong>3 lines to give your AI agent a file database and long-term memory.</strong><br/>
  Read any file. Search any workspace. Remember everything.
</p>

<p align="center">
  <a href="https://pypi.org/project/musedb/"><img src="https://img.shields.io/pypi/v/musedb" alt="PyPI version"/></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"/></a>
  <a href="https://www.gnu.org/licenses/agpl-3.0"><img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="License: AGPL v3"/></a>
  <a href="https://github.com/wuwangzhang1216/museDB/stargazers"><img src="https://img.shields.io/github/stars/wuwangzhang1216/museDB" alt="GitHub stars"/></a>
</p>

---

```bash
pip install musedb[cli]
musedb index ./my_workspace
musedb serve-mcp
```

That's it. Your agent now has 7 MCP tools — read any file format, search across documents and code, and store/recall persistent memories. Works with every major agent framework out of the box.

## Works with Every Agent Framework

MuseDB speaks [MCP](https://modelcontextprotocol.io/) — the universal standard supported by all major frameworks. Pick yours:

<details>
<summary><b>Claude Code / Cursor / Windsurf</b></summary>

Add to your MCP config (`.mcp.json`, `mcp_servers` in settings, etc.):

```json
{
  "mcpServers": {
    "musedb": {
      "command": "musedb",
      "args": ["serve-mcp", "--workspace", "/path/to/workspace"]
    }
  }
}
```

</details>

<details>
<summary><b>Claude Agent SDK (Anthropic)</b></summary>

```python
from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.mcp import MCPServerStdio

async with MCPServerStdio("musedb", ["serve-mcp", "--workspace", "./docs"]) as musedb:
    options = ClaudeAgentOptions(
        model="claude-sonnet-4-6",
        mcp_servers={"musedb": musedb},
        allowed_tools=["mcp__musedb__*"],
    )
    async for msg in query(prompt="Summarize the Q4 report", options=options):
        print(msg.content)
```

</details>

<details>
<summary><b>OpenAI Agents SDK</b></summary>

```python
from agents import Agent, Runner
from agents.mcp import MCPServerStdio

async with MCPServerStdio(name="musedb", params={
    "command": "musedb", "args": ["serve-mcp", "--workspace", "./docs"]
}) as musedb:
    agent = Agent(name="Analyst", model="gpt-4.1", mcp_servers=[musedb])
    result = await Runner.run(agent, "Find all revenue mentions in the PDF reports")
    print(result.final_output)
```

</details>

<details>
<summary><b>LangChain / LangGraph</b></summary>

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

async with MultiServerMCPClient({
    "musedb": {"command": "musedb", "args": ["serve-mcp", "--workspace", "./docs"], "transport": "stdio"}
}) as client:
    agent = create_react_agent("anthropic:claude-sonnet-4-6", await client.get_tools())
    result = await agent.ainvoke({"messages": [("user", "What changed in the latest spec?")]})
```

</details>

<details>
<summary><b>CrewAI</b></summary>

```python
from crewai import Agent, Task, Crew
from crewai.tools import MCPServerStdio

musedb = MCPServerStdio(command="musedb", args=["serve-mcp", "--workspace", "./docs"])

analyst = Agent(role="Document Analyst", goal="Analyze workspace files", mcps=[musedb])
task = Task(description="Summarize all PDF reports in the workspace", agent=analyst)
Crew(agents=[analyst], tasks=[task]).kickoff()
```

</details>

<details>
<summary><b>AutoGen (Microsoft)</b></summary>

```python
from autogen_ext.tools.mcp import mcp_server_tools, StdioServerParams
from autogen_agentchat.agents import AssistantAgent

tools = await mcp_server_tools(StdioServerParams(command="musedb", args=["serve-mcp", "--workspace", "./docs"]))
agent = AssistantAgent(name="analyst", model_client=client, tools=tools)
await agent.run("Search for deployment-related memories")
```

</details>

<details>
<summary><b>Google ADK</b></summary>

```python
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams

agent = LlmAgent(
    model="gemini-2.5-flash",
    name="analyst",
    tools=[McpToolset(connection_params=StdioConnectionParams(command="musedb", args=["serve-mcp", "--workspace", "./docs"]))],
)
```

</details>

<details>
<summary><b>Mastra (TypeScript)</b></summary>

```typescript
import { MCPClient } from "@mastra/mcp";
import { Agent } from "@mastra/core/agent";

const mcp = new MCPClient({
  servers: { musedb: { command: "musedb", args: ["serve-mcp", "--workspace", "./docs"] } },
});

const agent = new Agent({
  name: "Analyst",
  model: "openai/gpt-4.1",
  tools: await mcp.listTools(),
});
```

</details>

<details>
<summary><b>Python (direct, no framework)</b></summary>

```python
from musedb import MuseDB

db = MuseDB.open("./my_workspace")
await db.init()
await db.index()

text    = await db.read("report.pdf", pages="1-3")
results = await db.search("quarterly revenue")
await db.memory_store("User prefers concise answers")
memories = await db.memory_recall("user preferences")

await db.close()
```

</details>

## Why MuseDB?

Without MuseDB, agents write inline parsing code for every document:

```python
# Agent writes this every time — 500+ tokens, often fails
run_command("""python -c "
import PyMuPDF; doc = PyMuPDF.open('report.pdf')
for page in doc: print(page.get_text())
" """)
```

With MuseDB:

```python
read_file("report.pdf")  # 50 tokens, always works
```

**Benchmarked across 4 LLMs on 24 document tasks:**

| Metric | Without MuseDB | With MuseDB |
|--------|---------------|-------------|
| Tokens used | 100% | **27-45%** (55-73% saved) |
| Task speed | 100% | **36-58%** faster |
| Answer quality | 2.4-3.2 / 5 | **3.4-3.9 / 5** |
| Success rate | 79% | **100%** |

**FTS vs RAG vector retrieval (25-325 documents):**

| Scale | FTS Tokens Saved | FTS Quality | RAG Quality |
|-------|-----------------|------------|------------|
| 25 docs | **47%** | 3.9/5 | 4.2/5 |
| 125 docs | **44%** | **4.7/5** | 4.0/5 |
| 325 docs | **45%** | **4.6/5** | 3.5/5 |

FTS quality **improves with scale** while RAG degrades from distractor noise. See [benchmark/REPORT.md](benchmark/REPORT.md) for methodology.

## MCP Tools

7 tools, auto-discovered by any MCP-compatible agent:

### `musedb_info` — Workspace overview

```
musedb_info()
→ Workspace: 47 files (ready: 45, processing: 1, failed: 1)
  By type:  Python (.py) 20 | PDF 12 | Excel (.xlsx) 5 | ...
  Recently updated:  config.yaml (2 min ago) | main.py (1 hr ago)
```

### `musedb_read` — Read any file

Code with line numbers, documents as plain text, spreadsheets as structured JSON.

```
musedb_read(filename="main.py")                            # Code with line numbers
musedb_read(filename="report.pdf", pages="1-3")            # PDF pages
musedb_read(filename="report.pdf", grep="revenue+growth")  # Search within file
musedb_read(filename="budget.xlsx", format="json")          # Structured spreadsheet
musedb_read(filename="app.py", offset=50, limit=31)         # Lines 50-80
```

### `musedb_search` — Search across code and documents

Regex grep for code, full-text search for documents. Auto-detects mode.

```
musedb_search(query="def main", path="/workspace", glob="*.py")   # Grep code
musedb_search(query="quarterly revenue")                           # FTS documents
musedb_search(query="TODO", path="/src", case_insensitive=True)    # Case insensitive
```

### `musedb_glob` — Find files

```
musedb_glob(pattern="**/*.py", path="/workspace")
musedb_glob(pattern="src/**/*.{ts,tsx}", path="/workspace")
```

### `musedb_memory_store` — Store a memory

```
musedb_memory_store(content="User prefers dark mode", memory_type="semantic")
musedb_memory_store(content="Deployed v2.1, rollback required", memory_type="episodic", tags=["deploy"])
musedb_memory_store(content="Always run tests before merging", memory_type="procedural")
musedb_memory_store(content="User is a senior engineer at Acme", pinned=true)
```

Three memory types: **semantic** (facts/knowledge), **episodic** (events/outcomes), **procedural** (workflows/rules).

Set `pinned=true` for critical facts — they get 10x ranking boost and can be retrieved instantly with `pinned_only=true`.

### `musedb_memory_recall` — Search memories

Results ranked by **relevance × recency**. Pinned memories always surface first.

```
musedb_memory_recall(query="user preferences")
musedb_memory_recall(query="deploy", memory_type="episodic")
musedb_memory_recall(pinned_only=true)   # Instant — no search needed, ideal for agent startup
```

### `musedb_memory_forget` — Delete memories

```
musedb_memory_forget(memory_id="abc-123-def")
musedb_memory_forget(query="outdated preferences")
```

## Agent Memory

MuseDB doubles as a **long-term memory store** for AI agents — persistent across sessions, ranked by relevance and recency, with pinned priorities.

### Why not Markdown files?

| | Markdown files | MuseDB Memory |
|---|---|---|
| **Search** | Full-file scan, substring match | FTS5 BM25 index, O(log n) |
| **Ranking** | None — all matches are equal | Relevance × recency decay |
| **Capacity** | Claude Code: 200-line hard limit | No hard limit, indexed |
| **CJK** | Broken (no word segmentation) | jieba tokenization, native CJK |
| **Staleness** | Old = new, manual cleanup | `0.5^(age/30)` auto-decay |
| **Structure** | Free text + frontmatter | tags[], metadata{}, memory_type, pinned |
| **Agent cost** | Tokens spent on file management | 3 API calls: store/recall/forget |

### Why not vector databases?

FTS quality **improves with scale** while vector/RAG degrades. Vector similarity retrieves topically-similar noise; FTS retrieves exactly what the agent asked for.

### LongMemEval benchmark

Tested against [LongMemEval](https://github.com/xiaowu0162/LongMemEval) (ICLR 2025) — 470 questions across 6 types:

| | MuseDB (FTS5) | MemPalace (ChromaDB) |
|---|---|---|
| **R@5** | **100%** (470/470) | 96.6% |
| Embedding model | None (keyword index) | all-MiniLM-L6-v2 |
| API calls | 0 | 0 |
| Median recall latency | **0.9 ms** | — |
| Total benchmark time | **32 s** | ~5 min |

All 6 question types score 100%. Reproduce: `python benchmark/longmemeval_bench.py`

## Supported Formats

| Format | Extensions | Features |
|--------|-----------|----------|
| PDF | `.pdf` | Pages, tables, OCR for scanned docs |
| Word | `.docx` | Page breaks, tables, headings |
| PowerPoint | `.pptx` | Slides, speaker notes, tables |
| Excel | `.xlsx` | Multiple sheets, structured JSON output |
| CSV | `.csv` | Auto-encoding detection, structured JSON |
| Code | `.py` `.js` `.ts` `.go` `.rs` `.java` ... | Line-numbered output |
| Text | `.txt` `.md` `.html` `.json` `.xml` | Paragraph chunking |
| Images | `.png` `.jpg` `.tiff` `.bmp` | OCR (English + Chinese) |

## Key Features

- **3-line setup** — `pip install`, `index`, `serve-mcp` — works with every agent framework
- **7 MCP tools** — `read`, `search`, `glob`, `info` for files + `memory_store`, `memory_recall`, `memory_forget` for memory
- **Agent memory** — FTS + time-decay ranking, pinned memories, 100% on LongMemEval; no vector DB needed
- **Dual-mode** — Embedded (SQLite, zero-config) or Server (PostgreSQL, shared access); same API
- **Real-time sync** — Directories are watched via OS-native events after indexing
- **Full-text search** — FTS5 / tsvector with jieba CJK tokenization
- **Structured output** — Spreadsheets as `{sheets: [{columns, rows}]}` for direct analysis
- **Fuzzy filename resolution** — Find files by exact name, partial match, path, or UUID

## REST API

MuseDB also exposes a full HTTP API. Run with `musedb serve` (embedded) or `docker-compose up` (PostgreSQL).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/info` | `GET` | Workspace statistics |
| `/read/{filename}` | `GET` | Read file (`?pages=`, `?lines=`, `?grep=`, `?format=json`) |
| `/search` | `POST` | Full-text search or regex grep |
| `/glob` | `GET` | Find files by glob pattern |
| `/index` | `POST` | Index a directory and start watching |
| `/files` | `POST`/`GET` | Upload or list files |
| `/memory` | `POST`/`GET` | Store or list memories |
| `/memory/recall` | `POST` | Search memories with ranking |
| `/memory/forget` | `POST` | Delete memories |
| `/health` | `GET` | Health check |

## Configuration

Environment variables (`FILEDB_` prefix):

| Variable | Default | Description |
|----------|---------|-------------|
| `FILEDB_BACKEND` | `postgres` | `postgres` or `sqlite` |
| `FILEDB_DATABASE_URL` | `postgresql://...` | PostgreSQL connection |
| `FILEDB_OCR_ENABLED` | `true` | Enable Tesseract OCR |
| `FILEDB_OCR_LANGUAGES` | `eng+chi_sim+chi_tra` | OCR languages |
| `FILEDB_MAX_FILE_SIZE` | `104857600` | Max file size (100MB) |
| `FILEDB_INDEX_EXCLUDE_PATTERNS` | `[]` | Exclude patterns for indexing |
| `MUSEDB_URL` | `http://localhost:8000` | MCP server → REST API URL |

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

[AGPL-3.0](LICENSE) — Source code must be shared when running as a network service.
