"""OpenDB — AI-native file database.

Embedded usage::

    import asyncio
    from opendb import OpenDB

    async def main():
        db = OpenDB.open("./my_workspace")
        await db.init()
        await db.index()
        results = await db.search("quarterly revenue")
        print(results)
        await db.close()

    asyncio.run(main())
"""

from opendb_core.workspace import Workspace as OpenDB

__all__ = ["OpenDB"]
