<p align="center">
  <a href="https://github.com/wuwangzhang1216/openDB">
    <img loading="lazy" alt="OpenDB" src="https://github.com/wuwangzhang1216/openDB/raw/main/docs/assets/opendb-banner.svg" width="100%"/>
  </a>
</p>

<p align="center">
  <strong>3 lines to give your AI agent an AI-native database and long-term memory.</strong><br/>
  Read any file. Search any workspace. Remember everything.
</p>

<p align="center">
  <a href="https://pypi.org/project/open-db/"><img src="https://img.shields.io/pypi/v/open-db" alt="PyPI version"/></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"/></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"/></a>
  <a href="https://github.com/wuwangzhang1216/openDB/stargazers"><img src="https://img.shields.io/github/stars/wuwangzhang1216/openDB" alt="GitHub stars"/></a>
</p>

<p align="center">
  <b>93.6% on LongMemEval</b> — #3 on the leaderboard, beating MemMachine, Vectorize, Emergence AI, Supermemory, and Zep.<br/>
  Zero embedding APIs. Zero vector databases. Just SQLite FTS5 and good engineering.
</p>

---

```bash
pip install open-db[cli]
opendb index ./my_workspace
opendb serve-mcp
```

That's it. Your agent now has 12 MCP tools — read any file format, search across documents and code, store/recall persistent memories, and switch between multiple workspaces on the fly. Works with every major agent framework out of the box.

## LongMemEval Benchmark — 93.6%

OpenDB achieves **93.6% E2E accuracy** on [LongMemEval](https://github.com/xiaowu0162/LongMemEval) (ICLR 2025), the standard benchmark for AI agent long-term memory. 500 questions, 6 categories, LLM-as-judge evaluation.

| System | LongMemEval E2E | Gen Model | Retrieval Infrastructure |
|--------|:-:|-----------|--------------------------|
| OMEGA | 95.4% | GPT-4.1 | Embedding model + vector DB |
| Mastra | 94.9% | GPT-5-mini | LLM + embedding model |
| **OpenDB** | **93.6%** | **qwen3.6-plus** | **SQLite only, zero API** |
| MemMachine | 93.0% | — | LLM + vector DB |
| Vectorize Hindsight | 91.4% | — | Embedding model |
| Emergence AI | 86.0% | — | LLM + graph DB + vector DB |
| Supermemory | 81.6% | GPT-4o | Embedding model |
| Zep/Graphiti | 71.2% | — | Graph DB + LLM |

> OpenDB uses **qwen3.6-plus** — a significantly cheaper model than GPT-4.1 or GPT-5-mini. On the same system, Mastra showed a 10-point gap between GPT-4o (84%) and GPT-5-mini (95%), suggesting OpenDB with a frontier model would score even higher.

### Per-Category Results

| Category | OpenDB | OMEGA | Supermemory | Zep |
|----------|:------:|:-----:|:-----------:|:---:|
| single-session-assistant | **100%** | — | 96.4% | 80.4% |
| knowledge-update | **97.4%** | 96% | 88.5% | 83.3% |
| single-session-user | 97.1% | — | 97.1% | 92.9% |
| temporal-reasoning | **95.5%** | 94% | 76.7% | 62.4% |
| multi-session | **89.5%** | 83% | 71.4% | 57.9% |
| abstention | 86.7% | — | — | — |
| single-session-preference | 73.3% | — | 70.0% | 56.7% |

OpenDB **beats every competitor** on temporal-reasoning (95.5% vs OMEGA's 94%), knowledge-update (97.4% vs 96%), and multi-session (89.5% vs 83%) — without embeddings, without vector databases, without graph databases.

### Retrieval — 100% Recall

| | OpenDB (FTS5) | MemPalace (ChromaDB) |
|---|:---:|:---:|
| **R@5** | **100%** (470/470) | 96.6% |
| Embedding model | None | all-MiniLM-L6-v2 |
| API calls | 0 | 0 |
| Median recall latency | **1.1 ms** | — |

### How?

No embeddings. No vector search. No graph databases. Three things:

1. **SQLite FTS5** — BM25 keyword search with time-decay re-ranking. 1ms recall at 10K memories.
2. **Smart conflict detection** — Automatically supersedes outdated facts while preserving episodic event history.
3. **Temporal-aware prompting** — Memories sorted chronologically with real session dates, giving the LLM the context it needs for temporal reasoning.

Full methodology and per-question results: [benchmark/REPORT.md](benchmark/REPORT.md)

## Works with Every Agent Framework

OpenDB speaks [MCP](https://modelcontextprotocol.io/) — the universal standard supported by all major frameworks. Pick yours:

<details>
<summary><b>Claude Code / Cursor / Windsurf</b></summary>

Add to your MCP config (`.mcp.json`, `mcp_servers` in settings, etc.):

```json
{
  "mcpServers": {
    "opendb": {
      "command": "opendb",
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

async with MCPServerStdio("opendb", ["serve-mcp", "--workspace", "./docs"]) as opendb:
    options = ClaudeAgentOptions(
        model="claude-sonnet-4-6",
        mcp_servers={"opendb": opendb},
        allowed_tools=["mcp__opendb__*"],
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

async with MCPServerStdio(name="opendb", params={
    "command": "opendb", "args": ["serve-mcp", "--workspace", "./docs"]
}) as opendb:
    agent = Agent(name="Analyst", model="gpt-4.1", mcp_servers=[opendb])
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
    "opendb": {"command": "opendb", "args": ["serve-mcp", "--workspace", "./docs"], "transport": "stdio"}
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

opendb = MCPServerStdio(command="opendb", args=["serve-mcp", "--workspace", "./docs"])

analyst = Agent(role="Document Analyst", goal="Analyze workspace files", mcps=[opendb])
task = Task(description="Summarize all PDF reports in the workspace", agent=analyst)
Crew(agents=[analyst], tasks=[task]).kickoff()
```

</details>

<details>
<summary><b>AutoGen (Microsoft)</b></summary>

```python
from autogen_ext.tools.mcp import mcp_server_tools, StdioServerParams
from autogen_agentchat.agents import AssistantAgent

tools = await mcp_server_tools(StdioServerParams(command="opendb", args=["serve-mcp", "--workspace", "./docs"]))
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
    tools=[McpToolset(connection_params=StdioConnectionParams(command="opendb", args=["serve-mcp", "--workspace", "./docs"]))],
)
```

</details>

<details>
<summary><b>Mastra (TypeScript)</b></summary>

```typescript
import { MCPClient } from "@mastra/mcp";
import { Agent } from "@mastra/core/agent";

const mcp = new MCPClient({
  servers: { opendb: { command: "opendb", args: ["serve-mcp", "--workspace", "./docs"] } },
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
from opendb import OpenDB

db = OpenDB.open("./my_workspace")
await db.init()
await db.index()

text    = await db.read("report.pdf", pages="1-3")
results = await db.search("quarterly revenue")
await db.memory_store("User prefers concise answers")
memories = await db.memory_recall("user preferences")

await db.close()
```

</details>

## Build Your Own Agent (No Framework Needed)

You don't need a framework. A while loop, an LLM, and OpenDB — that's a complete agent:

```python
import json, asyncio
from anthropic import Anthropic
from opendb import OpenDB

client = Anthropic()
db = OpenDB.open("./workspace")

TOOLS = [
    {"name": "read",   "description": "Read a file",           "input_schema": {"type": "object", "properties": {"filename": {"type": "string"}}, "required": ["filename"]}},
    {"name": "search", "description": "Search across all files","input_schema": {"type": "object", "properties": {"query": {"type": "string"}},    "required": ["query"]}},
    {"name": "memory", "description": "Store a memory",         "input_schema": {"type": "object", "properties": {"content": {"type": "string"}},  "required": ["content"]}},
    {"name": "recall", "description": "Recall memories",        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},    "required": ["query"]}},
]

async def run(task: str):
    await db.init()
    await db.index()
    messages = [{"role": "user", "content": task}]

    while True:
        resp = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=4096,
            system="You have tools to read files, search, and remember things.",
            tools=TOOLS, messages=messages,
        )

        # Extract text and tool calls
        for block in resp.content:
            if block.type == "text":
                print(block.text)

        if resp.stop_reason == "end_turn":
            break

        # Execute tool calls and feed results back
        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                match block.name:
                    case "read":   result = await db.read(block.input["filename"])
                    case "search": result = await db.search(block.input["query"])
                    case "memory": result = await db.memory_store(block.input["content"])
                    case "recall": result = await db.memory_recall(block.input["query"])
                tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                     "content": json.dumps(result) if isinstance(result, dict) else str(result)})

        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content": tool_results})

    await db.close()

asyncio.run(run("Summarize the Q4 report and remember the key metrics"))
```

That's it. ~40 lines, zero abstractions, full agent capabilities. Swap `Anthropic()` for any LLM client — the pattern is the same.

## Why OpenDB?

Without OpenDB, agents write inline parsing code for every document:

```python
# Agent writes this every time — 500+ tokens, often fails
run_command("""python -c "
import PyMuPDF; doc = PyMuPDF.open('report.pdf')
for page in doc: print(page.get_text())
" """)
```

With OpenDB:

```python
read_file("report.pdf")  # 50 tokens, always works
```

**Benchmarked across 4 LLMs on 24 document tasks:**

| Metric | Without OpenDB | With OpenDB |
|--------|:-:|:-:|
| Tokens used | 100% | **27-45%** (55-73% saved) |
| Task speed | 100% | **36-58%** faster |
| Answer quality | 2.4-3.2 / 5 | **3.4-3.9 / 5** |
| Success rate | 79% | **100%** |

**FTS vs RAG vector retrieval (25-325 documents):**

| Scale | FTS Tokens Saved | FTS Quality | RAG Quality |
|-------|:-:|:-:|:-:|
| 25 docs | **47%** | 3.9/5 | 4.2/5 |
| 125 docs | **44%** | **4.7/5** | 4.0/5 |
| 325 docs | **45%** | **4.6/5** | 3.5/5 |

FTS quality **improves with scale** while RAG degrades from distractor noise. See [benchmark/REPORT.md](benchmark/REPORT.md) for methodology.

## MCP Tools

12 tools, auto-discovered by any MCP-compatible agent:

### `opendb_info` — Workspace overview

```
opendb_info()
-> Workspace: 47 files (ready: 45, processing: 1, failed: 1)
  By type:  Python (.py) 20 | PDF 12 | Excel (.xlsx) 5 | ...
  Recently updated:  config.yaml (2 min ago) | main.py (1 hr ago)
```

### `opendb_read` — Read any file

Code with line numbers, documents as plain text, spreadsheets as structured JSON.

```
opendb_read(filename="main.py")                            # Code with line numbers
opendb_read(filename="report.pdf", pages="1-3")            # PDF pages
opendb_read(filename="report.pdf", grep="revenue+growth")  # Search within file
opendb_read(filename="budget.xlsx", format="json")          # Structured spreadsheet
opendb_read(filename="app.py", offset=50, limit=31)         # Lines 50-80
```

### `opendb_search` — Search across code and documents

Regex grep for code, full-text search for documents. Auto-detects mode.

```
opendb_search(query="def main", path="/workspace", glob="*.py")   # Grep code
opendb_search(query="quarterly revenue")                           # FTS documents
opendb_search(query="TODO", path="/src", case_insensitive=True)    # Case insensitive
```

### `opendb_glob` — Find files

```
opendb_glob(pattern="**/*.py", path="/workspace")
opendb_glob(pattern="src/**/*.{ts,tsx}", path="/workspace")
```

### `opendb_memory_store` — Store a memory

```
opendb_memory_store(content="User prefers dark mode", memory_type="semantic")
opendb_memory_store(content="Deployed v2.1, rollback required", memory_type="episodic", tags=["deploy"])
opendb_memory_store(content="Always run tests before merging", memory_type="procedural")
opendb_memory_store(content="User is a senior engineer at Acme", pinned=true)
```

Three memory types: **semantic** (facts/knowledge), **episodic** (events/outcomes), **procedural** (workflows/rules).

Set `pinned=true` for critical facts — they get 10x ranking boost and can be retrieved instantly with `pinned_only=true`.

### `opendb_memory_recall` — Search memories

Results ranked by **relevance x recency**. Pinned memories always surface first.

```
opendb_memory_recall(query="user preferences")
opendb_memory_recall(query="deploy", memory_type="episodic")
opendb_memory_recall(pinned_only=true)   # Instant — no search needed, ideal for agent startup
```

### `opendb_memory_forget` — Delete memories

```
opendb_memory_forget(memory_id="abc-123-def")
opendb_memory_forget(query="outdated preferences")
```

### Workspace management — switch between projects on the fly

An agent working across multiple projects can list, add, and switch workspaces at runtime — **no server restart, sub-millisecond switching** after first open. The backend keeps each workspace's SQLite connection warm, so switching back and forth is just a pointer flip.

```
opendb_list_workspaces()
-> Active: [a3f2b1c8] openDB  (D:/work/openDB)
   Known workspaces (3):
   * [a3f2b1c8] openDB        D:/work/openDB       (last used 2026-04-10 14:22)
     [7d9e0422] my-notes      C:/Users/me/notes    (last used 2026-04-09 10:11)
     [e18a9f03] client-docs   D:/clients/acme      (last used 2026-04-08 17:45)

opendb_use_workspace(id_or_root="7d9e0422")         # Switch by id
opendb_use_workspace(id_or_root="D:/clients/acme")  # ...or by path
opendb_add_workspace(root="./new_project", switch=True)
opendb_current_workspace()
opendb_remove_workspace(id_or_root="e18a9f03")
```

Workspaces are persisted in `~/.opendb/workspaces.json` (override with `FILEDB_STATE_DIR`). Every `opendb_read` / `opendb_search` / `opendb_glob` / `opendb_memory_*` call targets the currently-active workspace.

## Agent Memory

OpenDB doubles as a **long-term memory store** for AI agents — persistent across sessions, ranked by relevance and recency, with pinned priorities.

### Why not Markdown files?

| | Markdown files | OpenDB Memory |
|---|---|---|
| **Search** | Full-file scan, substring match | FTS5 BM25 index, O(log n) |
| **Ranking** | None — all matches are equal | Relevance x recency decay |
| **Capacity** | Claude Code: 200-line hard limit | No hard limit, indexed |
| **CJK** | Broken (no word segmentation) | jieba tokenization, native CJK |
| **Staleness** | Old = new, manual cleanup | `0.5^(age/30)` auto-decay |
| **Structure** | Free text + frontmatter | tags[], metadata{}, memory_type, pinned |
| **Agent cost** | Tokens spent on file management | 3 API calls: store/recall/forget |

### Why not vector databases?

FTS quality **improves with scale** while vector/RAG degrades. Vector similarity retrieves topically-similar noise; FTS retrieves exactly what the agent asked for.

| | OpenDB (FTS) | Vector (cosine) |
|---|:---:|:---:|
| Recall accuracy | 90% | 100% |
| Recall latency | **0.57ms** | 223.76ms |
| Speed | **393x faster** | baseline |
| Embedding tokens | **0** | 454 |
| API calls | **0** | 21 |

The 10% accuracy gap comes from synonyms ("food allergy" vs "allergic to shellfish"). For everything else — keyword recall, temporal queries, knowledge updates, multi-session reasoning — FTS wins while costing nothing.

### Memory stress tests — 23/23 (100%)

| Suite | Result | Description |
|-------|--------|-------------|
| Knowledge Update | 5/5 | Conflict detection auto-supersedes stale facts |
| Abstention | 5/5 | FTS correctly returns empty for unrelated queries |
| Temporal Reasoning | 4/4 | Recency-biased ranking surfaces latest events |
| CJK Support | 5/5 | Chinese, Japanese, mixed CJK-English |
| Memory Scale (10K) | 4/4 | **0.5ms recall** at 10,000 memories |

### Document search scalability

| Documents | Needle Accuracy | Search p50 | Search p95 |
|-----------|:-:|:-:|:-:|
| 500 | 100% | **0.44ms** | 1.00ms |
| 1,000 | 100% | **0.62ms** | 1.99ms |
| 5,000 | 100% | **0.75ms** | 7.19ms |

Search time scales **sublinearly** (10x docs -> 1.7x latency).

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
- **12 MCP tools** — `read`, `search`, `glob`, `info` for files; `memory_store`, `memory_recall`, `memory_forget` for memory; `list_workspaces`, `use_workspace`, `add_workspace`, `remove_workspace`, `current_workspace` for multi-project workspace switching
- **Runtime workspace switching** — agents can list/add/switch workspaces at runtime with no server restart; already-opened workspaces switch in sub-millisecond
- **93.6% LongMemEval** — #3 on the leaderboard with a cheap model and zero retrieval infrastructure
- **100% R@5 retrieval** — Perfect memory recall, 1.1ms median latency, zero embedding API calls
- **Dual-mode** — Embedded (SQLite, zero-config) or Server (PostgreSQL, shared access); same API
- **Real-time sync** — Directories are watched via OS-native events after indexing
- **Full-text search** — FTS5 / tsvector with jieba CJK tokenization
- **Structured output** — Spreadsheets as `{sheets: [{columns, rows}]}` for direct analysis
- **Fuzzy filename resolution** — Find files by exact name, partial match, path, or UUID

## REST API

OpenDB also exposes a full HTTP API. Run with `opendb serve` (embedded) or `docker-compose up` (PostgreSQL).

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
| `/workspaces` | `GET`/`POST` | List or register workspaces |
| `/workspaces/active` | `GET`/`PUT` | Get or switch active workspace |
| `/workspaces/{id}` | `DELETE` | Unregister a workspace |
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
| `FILEDB_STATE_DIR` | `~/.opendb` | Location of the global workspace registry (`workspaces.json`) |
| `OPENDB_URL` | `http://localhost:8000` | MCP server -> REST API URL |

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

```bash
pip install -e ".[dev]"
pytest
```

## License

[MIT](LICENSE)
