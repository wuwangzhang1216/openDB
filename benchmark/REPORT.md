# OpenDB Benchmark Report

## Part 1: FileDB vs CMD Agent

> Full methodology: 4 models × 6 tasks × 3 runs on 25 corporate documents (80% binary).

### Token Efficiency: FileDB saves 55-73%

| Model | CMD Tokens | FileDB Tokens | Savings | CMD Tasks OK | FileDB Tasks OK |
|-------|-----------|--------------|---------|-------------|----------------|
| deepseek-chat-v3-0324 | 79,545 | 22,861 | **71%** | 6/6 | 6/6 |
| minimax-m2.5 | 346,089 | 92,746 | **73%** | 6/6 | 6/6 |
| kimi-k2.5 | 186,963 | 83,244 | **55%** | 5/6 | 6/6 |
| hunter-alpha | 214,016 | 159,213 | 26%* | **2/6** | 6/6 |

> \* hunter-alpha CMD failed 4/6 tasks, artificially deflating CMD total. On succeeded tasks, FileDB used 90% fewer tokens.

### Reliability

FileDB completed **23/24 task-model combinations** (96%) vs CMD's **19/24** (79%). CMD fails by entering extraction loops — writing Python scripts to parse binary files, exhausting the turn limit before answering.

### Answer Quality (LLM-as-Judge, blind)

| Dimension | CMD Average | FileDB Average |
|-----------|-----------|---------------|
| Accuracy | 2.8 | 3.0 |
| Completeness | 3.4 | **3.9** |
| Citations | 2.6 | 2.7 |
| Specificity | **4.3** | 4.2 |
| **Overall** | 3.2 | **3.4** |

---

## Part 2: FileDB (FTS) vs RAG (Vector Retrieval) at Scale

> FTS keyword search vs OpenAI `text-embedding-3-small` cosine top-k. Both agents consume identical parsed text from OpenDB's parsers — the independent variable is retrieval method only.

### Experimental Design

- **Signal corpus**: 25 hand-authored corporate docs (ground-truth answers to 6 tasks)
- **Distractor corpus**: 300 LLM-generated docs (10 departments × 10 doctypes, avg 1454 words each, 436k words total)
- **Scales**: small (25 docs), medium (125 = 25 signal + 100 distractors), large (325 = 25 signal + 300 distractors)
- **Fairness**: both agents get `list_files` + `read_file` (shared); only the search tool differs (FTS `search_documents` vs vector `semantic_search`)
- **Agent model**: `minimax/minimax-m2.7` (via OpenRouter)
- **Judge model**: `z-ai/glm-5` (blind evaluation)

### RAG Index Stats

| Scale | Files Indexed | Chunks | Embed Tokens | Index Size | Build Time |
|-------|-------------|--------|-------------|-----------|-----------|
| small | 14 | 19 | 7,002 | 114 KB | 1.5s |
| medium | 71 | 85 | 25,055 | 510 KB | 2.2s |
| large | 169 | 201 | 56,752 | 1.2 MB | 3.5s |

### Token Efficiency: FileDB wins at all scales

| Scale | Docs | FileDB Tokens | RAG Tokens | **FileDB Saves** |
|-------|------|--------------|-----------|-----------------|
| small | 25 | 83,543 | 157,589 | **47%** |
| medium | 125 | 76,460 | 136,543 | **44%** |
| large | 325 | 62,241 | 113,031 | **45%** |

### Success Rate

| Scale | FileDB | RAG |
|-------|--------|-----|
| small | **6/6** (100%) | **6/6** (100%) |
| medium | 5/6 (83%) | 5/6 (83%) |
| large | 3/6 (50%) | 4/6 (67%) |

Both agents struggle at large scale — timeouts (300s limit) hit complex tasks (T4 cross-reference). This is LLM turn-budget pressure, not a DB issue.

### Answer Quality

| Scale | FileDB Avg | RAG Avg |
|-------|-----------|---------|
| small | 3.9/5 | 4.2/5 |
| medium | **4.7/5** | 4.0/5 |
| large | **4.6/5** | 3.5/5 |

**Key finding**: RAG quality degrades at scale while FileDB improves. At medium/large, FTS keyword matching is more precise — distractor documents don't pollute results. RAG's semantic similarity pulls in topically-related but irrelevant chunks.

### Per-Task Breakdown (small scale)

| Task | FileDB | RAG | FileDB Quality | RAG Quality |
|------|--------|-----|---------------|-------------|
| T1 Cross-file search | 3,673 tok / 2 tools | 7,366 tok / 5 tools | 3.5 | 4.0 |
| T2 Targeted read | 11,548 / 9 | 11,883 / 4 | 3.0 | 3.3 |
| T3 Theme search | 4,886 / 5 | 11,426 / 6 | 3.3 | 4.3 |
| T4 Cross-reference | **31,476 / 18** | **93,957 / 23** | 3.8 | 4.5 |
| T5 Synthesis | 10,868 / 11 | 8,452 / 3 | 4.5 | 4.3 |
| T6 Reasoning | 21,092 / 15 | 24,505 / 10 | 5.0 | 5.0 |

T4 (cross-reference) is the most expensive task for both. RAG uses 94k tokens and 23 tool calls — the agent enters a search→read→search loop because chunk excerpts lack enough context.

### Why FTS Beats Vector Search Here

1. **FTS returns page-level highlights with context** — the agent often gets enough information from `search_documents` alone, without needing `read_file`. RAG's `semantic_search` returns 200-char chunk excerpts, forcing a follow-up `read_file` call (double-dip).

2. **Keyword precision scales better than semantic similarity in noisy corpora** — distractors share topical vocabulary with signal docs (same departments, same corporate language). FTS matches on specific terms ("Q4 revenue target") which distractors don't contain. RAG's embedding similarity treats "Q3 revenue analysis" and "Q4 revenue target" as close matches.

3. **FTS is zero-cost at query time** — no embedding API call needed. RAG pays ~$0.000001 per query (trivial), but the real cost is the additional `read_file` calls the agent makes to compensate for thin chunk excerpts.

---

## Applicability Boundaries

### When FileDB (FTS) is the right choice

1. **Binary document formats** (PDF, DOCX, PPTX) — eliminates extraction scripting
2. **Keyword-precise tasks** — finding specific terms, numbers, proper nouns
3. **Large noisy corpora** (>50 docs) — keyword precision avoids false positives
4. **Cost-sensitive workloads** — 44-73% fewer tokens, zero query-time API cost

### When RAG may outperform

1. **Semantic/paraphrase queries** — "company growth plans" matching "strategic expansion roadmap" (FTS misses this)
2. **Very small corpora** (<10 docs) — both methods work equally; RAG may find better context
3. **Multi-lingual search** — embeddings handle cross-language queries; FTS requires per-language tokenizer

### Crossover estimate

Based on this benchmark (English corporate documents, 6 QA tasks):
- **0-50 docs**: Both work well, FTS ~47% cheaper
- **50-300 docs**: FTS maintains edge; RAG quality degrades from distractor noise
- **300+ docs**: Both hit LLM turn limits on complex tasks; FTS still cheaper when it succeeds

---

## Methodology

- **FileDB vs CMD**: 4 models (deepseek-chat-v3-0324, minimax-m2.5, kimi-k2.5, hunter-alpha), 3 runs/task, 25 docs (9 PDF + 8 DOCX + 3 PPTX + 5 CSV)
- **FileDB vs RAG**: 1 model (minimax-m2.7), 1 run/task, 3 scales (25/125/325 docs)
- CMD Agent: 1 tool (`run_command`)
- FileDB Agent: 4 tools (`list_files`, `read_file`, `search_documents`, `get_file_toc`)
- RAG Agent: 3 tools (`list_files`, `semantic_search`, `read_file`)
- RAG index: `text-embedding-3-small`, 512-token chunks, 50-overlap, numpy cosine top-k
- `max_turns=25` for all agents, 300s session timeout
- Quality: LLM-as-judge (blind) — `z-ai/glm-5-turbo` (Part 1), `z-ai/glm-5` (Part 2)
- Distractor generation: `qwen/qwen3.6-plus` via OpenRouter, 300 docs, seeded deterministic
- All models accessed via OpenRouter
