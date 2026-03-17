"""MuseDB Integration Package.

Provides MuseDB-backed tools and index triggers for host applications.

Quick start:
    from musedb_integration import MuseDBClient, create_tools, ensure_indexed, index_file

    # 1. Create client
    client = MuseDBClient("http://localhost:8000")

    # 2. Register tools (replaces read/grep/glob)
    from your_app.tool.base import ToolDefinition, ToolResult
    for tool in create_tools(client, ToolDefinition, ToolResult):
        registry.register(tool)

    # 3. Trigger indexing on workspace selection
    asyncio.create_task(ensure_indexed(client, workspace_path))

    # 4. Trigger indexing on file upload
    asyncio.create_task(index_file(client, uploaded_file_path))
"""

from musedb_integration.client import MuseDBClient
from musedb_integration.index import ensure_indexed, index_file
from musedb_integration.tools import create_tools

__all__ = ["MuseDBClient", "create_tools", "ensure_indexed", "index_file"]
