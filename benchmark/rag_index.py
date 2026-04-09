"""In-memory RAG index over FileDB's parsed text.

Fetches text directly from FileDB's /files + /read endpoints to guarantee
identical parsed content to opendb's FTS. Embeds via OpenAI, stores in a
numpy matrix, queries by cosine similarity top-k.

No vector-DB dependency. No framework. Pure embeddings + numpy + httpx.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import numpy as np
from openai import AsyncOpenAI

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
    def _count_tokens(text: str) -> int:
        return len(_ENC.encode(text, disallowed_special=()))
    def _decode_tokens(ids: list[int]) -> str:
        return _ENC.decode(ids)
    def _encode_tokens(text: str) -> list[int]:
        return _ENC.encode(text, disallowed_special=())
except ImportError:
    # Fallback: approximate 1 token ≈ 4 chars
    def _count_tokens(text: str) -> int:
        return max(1, len(text) // 4)
    def _decode_tokens(ids) -> None:
        return "".join(ids)
    def _encode_tokens(text: str) -> None:
        # 400-char windows ≈ 100 tokens
        return [text[i:i+4] for i in range(0, len(text), 4)]


DEFAULT_CHUNK_TOKENS = 512
DEFAULT_OVERLAP_TOKENS = 50
EMBED_DIM = 1536  # text-embedding-3-small dimensionality


def chunk_text(text: str, chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
               overlap: int = DEFAULT_OVERLAP_TOKENS) -> list[tuple[int, str]]:
    """Split text into (start_token_offset, chunk_text) pairs."""
    if not text.strip():
        return []
    tokens = _encode_tokens(text)
    if len(tokens) <= chunk_tokens:
        return [(0, text)]
    chunks: list[tuple[int, str]] = []
    step = max(1, chunk_tokens - overlap)
    for start in range(0, len(tokens), step):
        window = tokens[start:start + chunk_tokens]
        if not window:
            break
        chunk = _decode_tokens(window)
        chunks.append((start, chunk))
        if start + chunk_tokens >= len(tokens):
            break
    return chunks


class RAGIndex:
    def __init__(self, embed_model: str = "openai/text-embedding-3-small",
                 api_base: str | None = None, api_key: str | None = None) -> None:
        self.embed_model = embed_model
        # Default to OpenRouter so we reuse the same key as the benchmark.
        self._client = AsyncOpenAI(
            api_key=api_key or os.environ.get("OPENROUTER_API_KEY")
                    or os.environ.get("OPENAI_API_KEY", ""),
            base_url=api_base or "https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://github.com/wuwangzhang1216/openDB",
                "X-Title": "openDB",
            },
        )
        self.vectors: np.ndarray | None = None  # (N, EMBED_DIM) normalized
        self.meta: list[dict] = []  # parallel list: {filename, char_start, chunk_text}
        self._build_seconds: float = 0.0
        self._embed_calls: int = 0
        self._embed_tokens: int = 0

    async def build(self, filedb_url: str, batch_size: int = 100) -> None:
        """Fetch parsed text via FileDB HTTP, chunk, embed, store."""
        t0 = time.perf_counter()
        async with httpx.AsyncClient(base_url=filedb_url, timeout=60.0) as client:
            # List all files
            # Paginate to get all files.
            all_files: list[dict] = []
            offset = 0
            while True:
                resp = await client.get("/files", params={"limit": 100, "offset": offset})
                resp.raise_for_status()
                batch = resp.json().get("files", [])
                if not batch:
                    break
                all_files.extend(batch)
                if len(batch) < 100:
                    break
                offset += len(batch)
            files = all_files
            print(f"  fetching {len(files)} files from FileDB...")

            all_chunks: list[tuple[str, int, str]] = []  # (filename, offset, text)
            for f in files:
                filename = f["filename"]
                r = await client.get(f"/read/{filename}", timeout=120.0)
                if r.status_code != 200:
                    print(f"    skip {filename}: HTTP {r.status_code}")
                    continue
                text = r.text
                chunks = chunk_text(text)
                for off, ch in chunks:
                    all_chunks.append((filename, off, ch))

        print(f"  chunked into {len(all_chunks)} pieces")

        # Embed in batches
        vecs: list[list[float]] = []
        for i in range(0, len(all_chunks), batch_size):
            batch = all_chunks[i:i + batch_size]
            inputs = [c[2] for c in batch]
            tok_count = sum(_count_tokens(x) for x in inputs)
            self._embed_tokens += tok_count
            self._embed_calls += 1
            for attempt in range(3):
                try:
                    resp = await self._client.embeddings.create(
                        model=self.embed_model, input=inputs,
                    )
                    break
                except Exception as e:
                    if attempt == 2:
                        raise
                    print(f"    embed batch {i} retry: {e}")
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
            vecs.extend([d.embedding for d in resp.data])
            print(f"  embedded {i + len(batch)}/{len(all_chunks)}")

        arr = np.asarray(vecs, dtype=np.float32)
        # Normalize for cosine similarity
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.vectors = arr / norms
        self.meta = [
            {"filename": fn, "char_start": off, "chunk_text": ct}
            for fn, off, ct in all_chunks
        ]
        self._build_seconds = time.perf_counter() - t0

    async def search(self, query: str, k: int = 5) -> list[dict]:
        if self.vectors is None or self.vectors.shape[0] == 0:
            return []
        resp = await self._client.embeddings.create(
            model=self.embed_model, input=[query],
        )
        qvec = np.asarray(resp.data[0].embedding, dtype=np.float32)
        qnorm = np.linalg.norm(qvec) or 1.0
        qvec = qvec / qnorm
        sims = self.vectors @ qvec  # (N,)
        k = min(k, sims.shape[0])
        top_idx = np.argpartition(-sims, k - 1)[:k]
        top_idx = top_idx[np.argsort(-sims[top_idx])]
        return [
            {
                "filename": self.meta[i]["filename"],
                "chunk_text": self.meta[i]["chunk_text"],
                "char_start": self.meta[i]["char_start"],
                "score": float(sims[i]),
            }
            for i in top_idx
        ]

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, vectors=self.vectors)
        meta_path = path.with_suffix(".json")
        meta_path.write_text(json.dumps({
            "embed_model": self.embed_model,
            "meta": self.meta,
            "build_seconds": self._build_seconds,
            "embed_calls": self._embed_calls,
            "embed_tokens": self._embed_tokens,
        }, ensure_ascii=False), encoding="utf-8")

    def load(self, path: Path) -> None:
        path = Path(path)
        data = np.load(path)
        self.vectors = data["vectors"]
        meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        self.meta = meta["meta"]
        self.embed_model = meta.get("embed_model", self.embed_model)
        self._build_seconds = meta.get("build_seconds", 0.0)
        self._embed_calls = meta.get("embed_calls", 0)
        self._embed_tokens = meta.get("embed_tokens", 0)

    def stats(self) -> dict:
        n = 0 if self.vectors is None else int(self.vectors.shape[0])
        files = len({m["filename"] for m in self.meta}) if self.meta else 0
        index_bytes = 0 if self.vectors is None else int(self.vectors.nbytes)
        return {
            "num_chunks": n,
            "num_files": files,
            "embed_calls": self._embed_calls,
            "embed_tokens": self._embed_tokens,
            "index_bytes": index_bytes,
            "build_seconds": round(self._build_seconds, 1),
            "embed_model": self.embed_model,
        }
