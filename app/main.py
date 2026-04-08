import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.services.watch_service import stop_all as stop_all_watchers
from app.routers.files import router as files_router
from app.routers.glob import router as glob_router
from app.routers.health import router as health_router
from app.routers.index import router as index_router
from app.routers.read import router as read_router
from app.routers.memory import router as memory_router
from app.routers.search import router as search_router
from app.services.read_service import AmbiguousFilenameError
from app.services.read_service import FileNotFoundError as FileDBNotFoundError
from app.storage import init_backend, close_backend


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    settings.file_storage_path.mkdir(parents=True, exist_ok=True)

    if settings.backend == "sqlite":
        db_path = settings.opendb_dir / "metadata.db"
        settings.opendb_dir.mkdir(parents=True, exist_ok=True)
        await init_backend("sqlite", db_path=db_path)
    else:
        # PostgreSQL: initialise pool first, then register backend
        from app.database import init_pool
        await init_pool()
        await init_backend("postgres")

    yield

    stop_all_watchers()
    await close_backend()

    if settings.backend == "postgres":
        from app.database import close_pool
        await close_pool()


app = FastAPI(
    title="OpenDB",
    description="cat + grep for any file format",
    version="1.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Optional API key auth — only active if FILEDB_AUTH_API_KEY is set
from opendb_core.middleware.auth import ApiKeyMiddleware
app.add_middleware(ApiKeyMiddleware, api_key=settings.auth_api_key if hasattr(settings, "auth_api_key") else "")


@app.exception_handler(FileDBNotFoundError)
async def file_not_found_handler(request: Request, exc: FileDBNotFoundError):
    return JSONResponse(status_code=404, content={"error": "file_not_found", "detail": str(exc)})


@app.exception_handler(AmbiguousFilenameError)
async def ambiguous_handler(request: Request, exc: AmbiguousFilenameError):
    return JSONResponse(
        status_code=409,
        content={"error": "ambiguous_filename", "candidates": exc.candidates},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"error": "bad_request", "detail": str(exc)})


app.include_router(health_router)
app.include_router(files_router)
app.include_router(glob_router)
app.include_router(index_router)
app.include_router(read_router)
app.include_router(search_router)
app.include_router(memory_router)


@app.get("/")
async def root():
    return {"service": "opendb", "version": "1.3.0"}
