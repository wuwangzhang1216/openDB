# Python Library & Agent Integration

## Embedded Mode (SQLite)

```bash
pip install opendb[cli]
```

```python
from opendb import OpenDB

db = OpenDB.open("./my_workspace")
await db.init()
await db.index()

# Workspace overview
stats = await db.info()

# Read any file
text = await db.read("report.pdf", pages="1-3")
text = await db.read("main.py", numbered=True)
data = await db.read("budget.xlsx", format="json")

# Search
results = await db.search("quarterly revenue")

# Find files
files = await db.glob("**/*.py")

await db.close()
```

## Server Mode (PostgreSQL)

```bash
pip install opendb[integration]
```

```python
from opendb_integration import OpenDBClient

# Server mode
db = OpenDBClient("postgresql://opendb:opendb@localhost:5432/opendb")
await db.init()

# Or embedded mode
db = OpenDBClient(workspace_path="./my_workspace")
await db.init()

# Same API as above
text = await db.read_file("report.pdf", pages="1-3")
results = await db.search("quarterly revenue")
files = await db.glob_files("**/*.py", path="/workspace")

await db.close()
```

## Agent Framework Integration

```python
from opendb_integration import OpenDBClient, create_tools
from your_app.tool import ToolDefinition, ToolResult  # your base classes

db = OpenDBClient(workspace_path="./my_workspace")
await db.init()

# Creates tools (read, grep, glob) that replace built-in tools.
# Falls back to local file operations when OpenDB is unavailable.
for tool in create_tools(db, ToolDefinition, ToolResult):
    registry.register(tool)
```

## REST API Tool Definitions

Copy-paste these into your agent for direct HTTP access:

```python
OPENDB = "http://localhost:8000"

def read_file(filename: str, pages: str = "", lines: str = "", grep: str = "", format: str = "", numbered: bool = False) -> str | dict:
    """Read a file as plain text (or structured JSON for spreadsheets)."""
    import httpx
    params = {k: v for k, v in {"pages": pages, "lines": lines, "grep": grep, "format": format}.items() if v}
    if numbered:
        params["numbered"] = "true"
    resp = httpx.get(f"{OPENDB}/read/{filename}", params=params)
    return resp.json() if format == "json" else resp.text

def search(query: str, mode: str = "fts", limit: int = 10, path: str = "", glob: str = "", case_insensitive: bool = False, context: int = 0) -> dict:
    """Search across files. FTS for documents, grep for code."""
    import httpx
    body = {"query": query, "mode": mode, "limit": limit}
    if path: body["path"] = path
    if glob: body["glob"] = glob
    if case_insensitive: body["case_insensitive"] = True
    if context: body["context"] = context
    return httpx.post(f"{OPENDB}/search", json=body).json()

def glob_files(pattern: str, path: str = "") -> dict:
    """Find files matching a glob pattern."""
    import httpx
    params = {"pattern": pattern}
    if path: params["path"] = path
    return httpx.get(f"{OPENDB}/glob", params=params).json()

def get_info() -> dict:
    """Get workspace statistics."""
    import httpx
    return httpx.get(f"{OPENDB}/info").json()
```

