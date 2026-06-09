# Phase 2 — Optional Hybrid Retrieval (FTS-first + local vectors)

Status: **planned** (Phase 1, the rank-aware FTS recall fix + CodeMemEval, is done).

## Why

Phase 1 made pure-FTS recall excellent on lexical workloads — 100% R@5 on both
LongMemEval and CodeMemEval, because coding memory is dominated by exact
identifiers (`CreateInvoice`, `:9090`, `pkg/gateway/middleware/auth.go`). FTS
wins there.

It still has one structural blind spot: **pure semantic / paraphrase recall**,
where the question and the answer share intent but no words. The benchmark already
exposes it:

- `memory_stress` → `conflicting_dates`: query *"frontend framework team"* must
  surface *"We migrated the frontend from React to **Svelte**"*. The newer fact
  only shares one token ("frontend"); it survives now only because Phase 1 keeps
  it as a candidate and time-decay ranks it. A query with **zero** lexical overlap
  would still miss.
- REPORT "When RAG may outperform": *"company growth plans"* vs *"strategic
  expansion roadmap"*.

The industry consensus in 2026 is hybrid (BM25 + dense) via Reciprocal Rank
Fusion: hybrid ≈ 91% recall@10 vs 65% sparse-only / 78% dense-only. We adopt it as
an **opt-in** layer so the default install stays zero-dependency and zero-API.

## Design principles

1. **Default unchanged.** No new dependency, no API, no behavior change unless the
   user opts in. `pip install open-db` stays 3-line and offline.
2. **Local, not cloud.** Embeddings computed locally (no embedding API), preserving
   the "zero API cost / deterministic / auditable" positioning.
3. **FTS-first.** FTS remains the primary signal; vectors only *add* recall for the
   semantic tail. Fuse with RRF so there is no brittle score normalization.

## Proposed implementation

### Storage
- Add an optional extra: `pip install open-db[hybrid]` →
  [`sqlite-vec`](https://github.com/asg017/sqlite-vec) (a tiny loadable SQLite
  extension; no server) + a local embedder.
- Embedder default: a small local model (e.g. `bge-small`/`all-MiniLM-L6-v2` via
  `fastembed`/ONNX — CPU-friendly, no torch). Pluggable so a user can point at any
  OpenAI-compatible embedding endpoint instead.
- New virtual table `memories_vec(rowid, embedding float[D])` populated on
  `store_memory` when hybrid is enabled. Backfill command for existing stores.

### Recall (`recall_memories`)
1. Run the existing FTS path → candidate list A (already ranked, time-decayed).
2. If hybrid enabled: embed the query, ANN-search `memories_vec` → candidate list B.
3. **RRF fuse**: `score(d) = Σ 1/(k + rank_i(d))` over A and B (k≈60), then apply
   the existing time-decay / confidence / pinned multipliers on the fused set.
4. Return top-N. Keep the rank-aware gate only on the FTS leg.

### Config
- `settings.retrieval_mode = "fts" | "hybrid"` (default `"fts"`).
- `settings.embed_model`, `settings.embed_dim`, `settings.rrf_k`.
- Env: `FILEDB_RETRIEVAL_MODE=hybrid`.

## Validation plan (must pass before shipping)

| Check | Target |
|---|---|
| LongMemEval R@5 | stays **100%** (no regression) |
| `conflicting_dates` + a new zero-overlap semantic stress case | pass |
| CodeMemEval E2E | ≥ current (92.6% cheap / 96.3% gpt-5.5) |
| New semantic-recall mini-suite (paraphrase Q/A) | hybrid > fts by a clear margin |
| Recall latency (hybrid, 10k memories) | < 10 ms p95 (ANN is the cost) |
| Default (`mode=fts`) install | unchanged: no new import, no API |

## Rollout
1. Land `[hybrid]` extra + schema + backfill behind `mode="fts"` default.
2. Add a `semantic_recall` benchmark suite (paraphrase Q/A with zero lexical
   overlap) to quantify the gain.
3. Document the tradeoff in REPORT "Applicability Boundaries" (when to enable).
4. Only after the validation table is green, consider making hybrid the default
   for `memory` (never for code/identifier search, where FTS is strictly better).
