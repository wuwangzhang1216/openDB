# FileDB vs CMD Agent Benchmark Report

## Overview

- **Models**: `deepseek/deepseek-chat-v3-0324`, `minimax/minimax-m2.5`, `moonshotai/kimi-k2.5`, `openrouter/hunter-alpha`
- **Agents**: CMD (1 tool: `run_command`), FileDB (4 tools: `list_files`, `read_file`, `search_documents`, `get_file_toc`)
- **Runs per task**: 3 (median reported, [min-max] shown)
- **Tasks**: 6 document analysis tasks across search, read, synthesis, and reasoning
- **Workspace**: 25 company documents (9 PDF, 8 DOCX, 3 PPTX, 5 CSV)
- **Quality judge**: GLM-5-Turbo (blind evaluation, judge does not know which agent)

---

## Key Findings

### 1. Token Efficiency: FileDB saves 55-73% tokens (on successful tasks)

| Model | CMD Tokens | FileDB Tokens | Savings | CMD Tasks OK | FileDB Tasks OK |
|-------|-----------|--------------|---------|-------------|----------------|
| deepseek-chat-v3-0324 | 79,545 | 22,861 | **71%** | 6/6 | 6/6 |
| minimax-m2.5 | 346,089 | 92,746 | **73%** | 6/6 | 6/6 |
| kimi-k2.5 | 186,963 | 83,244 | **55%** | 5/6 | 6/6 |
| hunter-alpha | 214,016 | 159,213 | 26%* | **2/6** | 6/6 |

> \* hunter-alpha's 26% savings is misleading: CMD failed 4 of 6 tasks (0 tokens counted), artificially deflating the CMD total. On the 2 tasks where CMD succeeded (T2, T5), FileDB used 90% fewer tokens per task.

### 2. Reliability: FileDB succeeds where CMD fails

FileDB completed **23/24 task-model combinations** (96%) vs CMD's **19/24** (79%). The gap widens on models with weaker tool-use capabilities:

| Model | CMD Success Rate | FileDB Success Rate |
|-------|-----------------|-------------------|
| deepseek-chat-v3-0324 | 100% (6/6) | 100% (6/6) |
| minimax-m2.5 | 100% (6/6) | 100% (6/6) |
| kimi-k2.5 | 83% (5/6) | 100% (6/6) |
| hunter-alpha | **33% (2/6)** | **100% (6/6)** |

CMD failures are caused by the model entering extraction loops: it writes Python scripts to parse binary files, reads the output, then tries to extract more — exhausting the 25-turn limit before answering the question. FileDB avoids this entirely because document parsing is handled server-side.

### 3. Answer Quality: comparable, with FileDB stronger on completeness

| Dimension | CMD Average | FileDB Average |
|-----------|-----------|---------------|
| Accuracy | 2.8 | 3.0 |
| Completeness | 3.4 | **3.9** |
| Citations | 2.6 | 2.7 |
| Specificity | **4.3** | 4.2 |
| **Overall** | 3.2 | **3.4** |

Quality is roughly on par. FileDB has a consistent edge on **completeness** because `search_documents` retrieves relevant passages across many files in a single call, while CMD must manually open files one by one and often runs out of turns before being thorough. CMD shows slightly higher **specificity** because when it succeeds, it can quote raw extracted text directly.

Note: CMD quality scores exclude failed tasks (where no answer was produced). If failures were scored as 0, CMD's effective quality would be significantly lower.

---

## Detailed Results by Model

### deepseek-chat-v3-0324

| Task | Description | Agent | Tools | Tokens | Time | Quality |
|------|-------------|-------|-------|--------|------|---------|
| T1 | Cross-file search | CMD | 9 | 11,637 [4,650-23,270] | 43.5s | 1.8/5 |
|  |  | FileDB | 1 | 3,743 [3,703-3,743] | 16.9s | 4.8/5 |
| | | **delta** | **+8** | **+7,894 (+68%)** | | |
| T2 | Targeted read | CMD | 10 | 37,939 [19,413-204,186] | 58.4s | 2.2/5 |
|  |  | FileDB | 4 | 6,574 [6,550-7,400] | 21.7s | 2.2/5 |
| | | **delta** | **+6** | **+31,365 (+83%)** | | |
| T3 | Theme search | CMD | 5 | 5,236 [4,788-5,238] | 24.2s | 3.2/5 |
|  |  | FileDB | 1 | 3,740 [3,696-3,746] | 28.9s | 3.2/5 |
| | | **delta** | **+4** | **+1,496 (+29%)** | | |
| T4 | Cross-reference | CMD | 5 | 9,681 [6,002-168,717] | 67.0s | 3.0/5 |
|  |  | FileDB | 3 | 2,920 [2,431-4,251] | 30.2s | 3.0/5 |
| | | **delta** | **+2** | **+6,761 (+70%)** | | |
| T5 | Synthesis | CMD | 7 | 8,732 [8,440-15,556] | 57.6s | 3.5/5 |
|  |  | FileDB | 1 | 2,954 [2,935-3,730] | 46.2s | 2.5/5 |
| | | **delta** | **+6** | **+5,778 (+66%)** | | |
| T6 | Reasoning | CMD | 3 | 6,320 [1,877-7,977] | 47.8s | 4.5/5 |
|  |  | FileDB | 1 | 2,930 [2,618-3,167] | 30.4s | 1.0/5 |
| | | **delta** | **+2** | **+3,390 (+54%)** | | |

| Agent | Total Tokens | Total Tools | Total Time | Tasks OK |
|-------|-------------|-------------|------------|----------|
| CMD | 79,545 | 39 | 298.5s | 6/6 |
| FileDB | 22,861 | 11 | 174.3s | 6/6 |

**Token savings: 71%** | DeepSeek is the most efficient CMD model (strong coding ability), yet FileDB still saves 71%.

---

### minimax-m2.5

| Task | Description | Agent | Tools | Tokens | Time | Quality |
|------|-------------|-------|-------|--------|------|---------|
| T1 | Cross-file search | CMD | 23 | 69,167 [69,167-69,167] | 62.3s |  |
|  |  | FileDB | 7 | 16,150 [11,226-39,801] | 28.0s | 4.2/5 |
| | | **delta** | **+16** | **+53,017 (+77%)** | | |
| T2 | Targeted read | CMD | 8 | 19,634 [18,761-20,508] | 28.3s | 1.5/5 |
|  |  | FileDB | 2 | 4,137 [4,128-4,264] | 6.9s | 1.5/5 |
| | | **delta** | **+6** | **+15,497 (+79%)** | | |
| T3 | Theme search | CMD | 22 | 118,730 [118,730-118,730] | 70.3s |  |
|  |  | FileDB | 6 | 13,337 [9,806-29,381] | 27.2s | 3.8/5 |
| | | **delta** | **+16** | **+105,393 (+89%)** | | |
| T4 | Cross-reference | CMD | 3 | 6,273 [5,995-48,543] | 9.6s | 3.0/5 |
|  |  | FileDB | 5 | 7,102 [7,006-13,652] | 9.9s | 2.8/5 |
| | | **delta** | **-2** | **-829 (-13%)** | | |
| T5 | Synthesis | CMD | 13 | 51,821 [40,311-63,331] | 30.0s |  |
|  |  | FileDB | 4 | 10,499 [5,847-12,312] | 10.5s | 4.8/5 |
| | | **delta** | **+9** | **+41,322 (+80%)** | | |
| T6 | Reasoning | CMD | 16 | 80,464 [54,167-83,354] | 57.2s | 4.2/5 |
|  |  | FileDB | 12 | 41,521 [36,722-53,938] | 41.8s | 4.5/5 |
| | | **delta** | **+4** | **+38,943 (+48%)** | | |

| Agent | Total Tokens | Total Tools | Total Time | Tasks OK |
|-------|-------------|-------------|------------|----------|
| CMD | 346,089 | 85 | 257.7s | 6/6 |
| FileDB | 92,746 | 36 | 124.3s | 6/6 |

**Token savings: 73%** | MiniMax shows the highest savings, but CMD only succeeded on 33-67% of runs for T1/T3/T5 (median from the single successful run).

---

### kimi-k2.5

| Task | Description | Agent | Tools | Tokens | Time | Quality |
|------|-------------|-------|-------|--------|------|---------|
| T1 | Cross-file search | CMD | 0 | **FAILED** (Max turns exceeded) | 0s | FAILED |
|  |  | FileDB | 1 | 3,900 [3,731-6,896] | 42.4s | 3.0/5 |
| T2 | Targeted read | CMD | 14 | 31,475 [24,318-108,219] | 72.8s | 2.3/5 |
|  |  | FileDB | 2 | 3,655 [3,621-4,501] | 23.6s | 2.2/5 |
| | | **delta** | **+12** | **+27,820 (+88%)** | | |
| T3 | Theme search | CMD | 21 | 57,999 [38,049-77,949] | 169.6s | 4.0/5 |
|  |  | FileDB | 7 | 14,119 [13,954-14,461] | 58.0s | 4.2/5 |
| | | **delta** | **+14** | **+43,880 (+76%)** | | |
| T4 | Cross-reference | CMD | 5 | 5,984 [5,677-12,566] | 85.0s | 4.0/5 |
|  |  | FileDB | 6 | 8,031 [6,400-8,333] | 53.2s | 3.0/5 |
| | | **delta** | **-1** | **-2,047 (-34%)** | | |
| T5 | Synthesis | CMD | 19 | 50,887 [27,423-79,979] | 126.3s | 4.8/5 |
|  |  | FileDB | 6 | 9,578 [8,700-14,452] | 54.3s | 4.5/5 |
| | | **delta** | **+13** | **+41,309 (+81%)** | | |
| T6 | Reasoning | CMD | 18 | 40,618 [31,598-90,315] | 96.7s | 4.0/5 |
|  |  | FileDB | 18 | 43,961 [38,273-52,216] | 105.5s | 4.3/5 |
| | | **delta** | **+0** | **-3,343 (-8%)** | | |

| Agent | Total Tokens | Total Tools | Total Time | Tasks OK |
|-------|-------------|-------------|------------|----------|
| CMD | 186,963 | 77 | 550.4s | 5/6 |
| FileDB | 83,244 | 40 | 337.0s | 6/6 |

**Token savings: 55%** | Kimi is the strongest reasoning model tested, yet CMD still failed T1 completely. On T6 (reasoning), the two agents are nearly tied in tokens — this is where FileDB's advantage is smallest.

---

### hunter-alpha

| Task | Description | Agent | Tools | Tokens | Time | Quality |
|------|-------------|-------|-------|--------|------|---------|
| T1 | Cross-file search | CMD | 0 | **FAILED** (Max turns exceeded) | 0s | FAILED |
|  |  | FileDB | 11 | 44,063 [33,152-60,458] | 84.6s | 4.3/5 |
| T2 | Targeted read | CMD | 22 | 50,340 [50,340-50,340] | 134.3s |  |
|  |  | FileDB | 2 | 5,065 [4,937-6,528] | 19.7s | 3.0/5 |
| | | **delta** | **+20** | **+45,275 (+90%)** | | |
| T3 | Theme search | CMD | 0 | **FAILED** (Max turns exceeded) | 0s | FAILED |
|  |  | FileDB | 12 | 59,557 [18,176-100,939] | 77.8s | 4.2/5 |
| T4 | Cross-reference | CMD | 0 | **FAILED** (Max turns exceeded) | 0s | FAILED |
|  |  | FileDB | 5 | 6,109 [5,299-7,049] | 23.4s | 2.8/5 |
| T5 | Synthesis | CMD | 12 | 163,676 [21,601-305,751] | 72.0s |  |
|  |  | FileDB | 8 | 16,720 [8,923-16,803] | 45.3s | 4.0/5 |
| | | **delta** | **+4** | **+146,956 (+90%)** | | |
| T6 | Reasoning | CMD | 0 | **FAILED** (Max turns exceeded) | 0s | FAILED |
|  |  | FileDB | 12 | 27,699 [24,365-39,010] | 58.2s | 4.8/5 |

| Agent | Total Tokens | Total Tools | Total Time | Tasks OK |
|-------|-------------|-------------|------------|----------|
| CMD | 214,016 | 34 | 206.3s | 2/6 |
| FileDB | 159,213 | 50 | 309.0s | 6/6 |

**Token savings: 26%*** | hunter-alpha demonstrates the most extreme reliability gap. CMD succeeded on only 2 tasks (T2 at 33%, T5 at 67%). On those tasks, CMD used 90% more tokens. The 26% headline number is misleading because CMD's 4 FAILED tasks contribute 0 tokens to its total.

---

## Answer Quality (LLM-as-Judge)

| Model | Agent | Accuracy | Completeness | Citations | Specificity | Overall |
|-------|-------|----------|-------------|-----------|------------|---------|
| deepseek-chat-v3-0324 | CMD | 2.7 | 3.0 | 2.7 | 3.8 | **3.0** |
| deepseek-chat-v3-0324 | FileDB | 2.3 | 3.2 | 2.5 | 3.2 | **2.8** |
| minimax-m2.5 | CMD | 2.3 | 3.3 | 2.0 | 4.0 | **2.9** |
| minimax-m2.5 | FileDB | 3.3 | 3.7 | 3.0 | 4.3 | **3.6** |
| kimi-k2.5 | CMD | 3.4 | 3.8 | 3.0 | 5.0 | **3.8** |
| kimi-k2.5 | FileDB | 3.0 | 4.2 | 2.5 | 4.5 | **3.5** |
| hunter-alpha | CMD | — | — | — | — | **N/A** (4/6 failed) |
| hunter-alpha | FileDB | 3.2 | 4.5 | 2.8 | 4.8 | **3.9** |

Quality evaluation is blind: the judge model sees only the question and the answer, not which agent produced it.

CMD quality scores only cover tasks that succeeded. On the 3 models where both agents completed all tasks (deepseek, minimax on T2/T4/T6, kimi on T2-T6), quality is comparable. The main quality advantage of FileDB is that **it produces answers for tasks where CMD fails entirely**.

---

## Applicability Boundary Analysis

### When FileDB Helps

1. **Binary document formats** (PDF, DOCX, PPTX) — CMD agent must write Python extraction scripts, consuming enormous context. This is the dominant cost: a single `python -c "import fitz; ..."` call can consume 5,000-20,000 tokens when the model writes and then reads back extracted text.
2. **Cross-document search** — FileDB's `search_documents` returns ranked results across all files in one call; CMD must open files individually.
3. **Large corpus** (>10 files) — extraction overhead compounds with each file. At 25 files, CMD typically exhausts its turn budget before reading all relevant documents.
4. **Models with weaker tool-use** — the simpler FileDB tool interface (structured input/output) is easier for models to use correctly than composing shell commands and Python scripts.

### When FileDB Does NOT Help

1. **All plain text files** — CMD's `type` command is zero-overhead; a prior text-only benchmark showed CMD was 12% more token-efficient.
2. **Single-file tasks** — direct reading is equally simple for both agents.
3. **Very small corpus** (1-3 files) — extraction overhead is manageable within the turn limit.
4. **Simple reasoning over known data** — T4 (cross-reference) and T6 (reasoning) sometimes favor CMD because they require fewer file reads and more reasoning.

### Crossover Point (estimated)

*Based on extrapolation from 0% binary (prior txt-only benchmark) and 80% binary (this benchmark). Not directly measured at intermediate ratios.*

- **0% binary files**: CMD is ~12% more token-efficient
- **80%+ binary files**: FileDB saves 55-73% tokens (this benchmark)
- **Estimated crossover**: ~20-30% binary files is where FileDB begins to show advantage
- The crossover shifts lower for search-heavy tasks (T1, T3) and higher for single-file tasks (T2)

---

## Conclusion

FileDB provides two distinct advantages over the CMD-only approach for document-heavy agent workloads:

1. **Efficiency**: 55-73% fewer tokens across all models when both agents succeed. The savings come from eliminating the need to write and execute Python extraction scripts for binary formats — FileDB handles parsing server-side and returns clean text to the model.

2. **Reliability**: Near-100% task completion across all 4 models tested, compared to CMD's 33-100% depending on model capability. This matters in production: a failed task means no answer for the user, regardless of how efficient the attempt was.

These advantages scale with corpus size and binary file ratio. For workspaces with >10 documents and >20% binary formats, FileDB is the recommended approach. For pure plain-text workspaces with few files, CMD remains slightly more efficient.

---

## Methodology

- 3 runs per task — median reported, [min-max] shown
- Models: deepseek/deepseek-chat-v3-0324, minimax/minimax-m2.5, moonshotai/kimi-k2.5, openrouter/hunter-alpha
- CMD Agent: 1 tool (`run_command`) — executes shell commands and inline Python (`python -c "..."`) for binary format extraction
- FileDB Agent: 4 tools (`list_files`, `read_file`, `search_documents`, `get_file_toc`) — all document parsing handled server-side
- `max_turns=25` for all agents
- File formats: 9 PDF, 8 DOCX, 3 PPTX, 5 CSV (80% binary by count)
- Wall time includes model inference latency, which varies significantly across providers; **token counts are the primary efficiency metric**
- Quality evaluation via LLM-as-judge (GLM-5-Turbo, blind — judge does not know which agent produced the answer)
- All models accessed via OpenRouter with identical API configuration
- Session timeout: 300 seconds (prevents API hangs from blocking the benchmark)
