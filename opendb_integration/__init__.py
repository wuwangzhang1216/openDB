"""OpenDB Integration Package.

Provides OpenDB-backed tools and index triggers for host applications.

Quick start:
    from opendb_integration import OpenDBClient, create_tools, ensure_indexed, index_file

    # 1. Create client
    client = OpenDBClient("http://localhost:8000")

    # 2. Register tools (replaces read/grep/glob)
    from your_app.tool.base import ToolDefinition, ToolResult
    for tool in create_tools(client, ToolDefinition, ToolResult):
        registry.register(tool)

    # 3. Trigger indexing on workspace selection
    asyncio.create_task(ensure_indexed(client, workspace_path))

    # 4. Trigger indexing on file upload
    asyncio.create_task(index_file(client, uploaded_file_path))
"""

from opendb_integration.client import OpenDBClient
from opendb_integration.index import ensure_indexed, index_file
from opendb_integration.tools import create_tools

__all__ = ["OpenDBClient", "create_tools", "ensure_indexed", "index_file"]
