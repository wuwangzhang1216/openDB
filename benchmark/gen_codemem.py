#!/usr/bin/env python3
"""
CodeMemEval dataset generator
=============================================================================
Builds a coding-agent long-term-memory benchmark in the *exact* LongMemEval
schema, so it plugs straight into the existing harnesses:

    python longmemeval_bench.py      --data codemem_dataset.json   # Recall@K
    python longmemeval_e2e_bench.py  --data codemem_dataset.json   # E2E acc

Why a separate benchmark? LongMemEval measures *conversational* memory
(personal facts, preferences, life events). A coding agent needs a different
kind of durable memory: architecture decisions, code conventions, API
signatures, past bug fixes, where things live in the repo, and — critically —
which of those facts are still *current* after the codebase evolves.

Design for ground-truth integrity:
  * Every fact, question, and gold answer is hand-authored here in Python, so
    we have full control over correctness (no LLM-invented ground truth).
  * The LLM only *renders* each fact into a realistic developer<->agent
    session transcript that naturally contains the fact. Rendering is cached
    and resumable (codemem_sessions_cache.json), so re-runs are nearly free.

Question types (reported per-category by the harness):
  code-architecture  — durable design/architecture decisions
  code-convention    — coding standards / procedural rules
  api-signature      — function / endpoint signatures and params
  bug-fix            — past bugs and their fixes (episodic)
  code-location      — where a thing lives in the repo
  knowledge-update   — a decision/convention that CHANGED; gold = newest state
  multi-session      — answer requires combining facts from 2+ sessions
  abstention (_abs)  — info not in memory; agent must decline (anti-hallucination)

Usage:
    python gen_codemem.py [--model gpt-5.5] [--distractors 40] [--out codemem_dataset.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from pathlib import Path

from dotenv import load_dotenv

BENCHMARK_DIR = Path(__file__).resolve().parent
load_dotenv(BENCHMARK_DIR / ".env")

from openai import AsyncOpenAI

_client = AsyncOpenAI(
    base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    api_key=os.environ.get("OPENROUTER_API_KEY", os.environ.get("OPENAI_API_KEY", "")),
)

# Deterministic haystack assembly (rendering uses the LLM, assembly does not).
RNG = random.Random(1216)

# A coherent fictional platform so facts cross-reference realistically.
PROJECT = "Helios, a cloud microservices platform for logistics (orders, billing, routing, fleet telemetry)."

# ---------------------------------------------------------------------------
# Hand-authored durable engineering facts. ground truth lives here.
# ---------------------------------------------------------------------------
# Single-evidence facts: one session establishes the fact.
FACTS: list[dict] = [
    # --- code-architecture ---
    {"id": "arch_billing_db", "type": "code-architecture",
     "fact": "The billing-service uses PostgreSQL 15 as its primary datastore. It was chosen over MongoDB specifically because invoice generation needs ACID multi-row transactions.",
     "question": "Which database does the billing-service use, and why was that chosen over the alternative?",
     "answer": "PostgreSQL 15, chosen over MongoDB because invoicing needs ACID multi-row transactions."},
    {"id": "arch_event_bus", "type": "code-architecture",
     "fact": "Inter-service events in Helios flow through NATS JetStream. Kafka was explicitly rejected because the team wanted lighter operational overhead and at-most-once was acceptable for telemetry.",
     "question": "What message bus do Helios services use for events, and why not Kafka?",
     "answer": "NATS JetStream; Kafka was rejected for being heavier operationally while at-most-once delivery was acceptable for telemetry."},
    {"id": "arch_auth", "type": "code-architecture",
     "fact": "Authentication across Helios is handled by short-lived JWTs (15 minute expiry) issued by the identity-service, validated at the API gateway with the public JWKS endpoint. Services never call the identity-service to validate tokens.",
     "question": "How are auth tokens validated across Helios services?",
     "answer": "Short-lived 15-minute JWTs issued by identity-service, validated at the API gateway against the JWKS endpoint; services don't call identity-service to validate."},
    {"id": "arch_routing_cache", "type": "code-architecture",
     "fact": "The routing-service caches computed delivery routes in Redis with a 6-hour TTL, keyed by (origin_hub, dest_hub, vehicle_class).",
     "question": "How does the routing-service cache delivery routes?",
     "answer": "In Redis with a 6-hour TTL, keyed by (origin_hub, dest_hub, vehicle_class)."},

    # --- code-convention ---
    {"id": "conv_error", "type": "code-convention",
     "fact": "Convention: every service returns errors as RFC 7807 problem+json. Handlers must never return a bare string error; they wrap with the shared `problem.New(status, detail)` helper.",
     "question": "What is the error-response convention for Helios services?",
     "answer": "RFC 7807 problem+json via the shared problem.New(status, detail) helper; never bare string errors."},
    {"id": "conv_naming", "type": "code-convention",
     "fact": "Convention: HTTP handler functions are named with a `handle` prefix and the resource in PascalCase, e.g. handleCreateInvoice, handleListShipments. Linter rule lint-handler-name enforces this.",
     "question": "What is the naming convention for HTTP handler functions?",
     "answer": "A `handle` prefix plus the resource in PascalCase (e.g. handleCreateInvoice), enforced by the lint-handler-name rule."},
    {"id": "conv_migrations", "type": "code-convention",
     "fact": "Convention: database migrations use golang-migrate, are append-only, and must be reversible — every up migration ships with a matching down migration. Editing an already-merged migration is forbidden.",
     "question": "What are the rules for writing database migrations in Helios?",
     "answer": "Use golang-migrate, append-only and reversible (every up has a matching down); never edit an already-merged migration."},
    {"id": "conv_tests", "type": "code-convention",
     "fact": "Convention: integration tests run against ephemeral Postgres/Redis spun up with testcontainers-go. Mocks for the datastore layer are banned in integration tests; only unit tests may mock.",
     "question": "How are integration tests expected to handle datastore dependencies?",
     "answer": "Use real ephemeral Postgres/Redis via testcontainers-go; datastore mocks are banned in integration tests (only unit tests may mock)."},

    # --- api-signature ---
    {"id": "api_create_invoice", "type": "api-signature",
     "fact": "The billing client exposes CreateInvoice(ctx, customerID string, lineItems []LineItem, opts ...InvoiceOption) (*Invoice, error). The idempotency key is passed via WithIdempotencyKey(key) as an opt.",
     "question": "What is the signature of CreateInvoice and how is the idempotency key supplied?",
     "answer": "CreateInvoice(ctx, customerID string, lineItems []LineItem, opts ...InvoiceOption) (*Invoice, error); the idempotency key is passed via the WithIdempotencyKey(key) option."},
    {"id": "api_route_endpoint", "type": "api-signature",
     "fact": "The routing-service HTTP endpoint to compute a route is POST /v2/routes with JSON body {origin_hub, dest_hub, vehicle_class, depart_after}. It returns 200 with {route_id, legs[], eta_seconds}.",
     "question": "What is the endpoint and request body to compute a delivery route?",
     "answer": "POST /v2/routes with body {origin_hub, dest_hub, vehicle_class, depart_after}; returns {route_id, legs[], eta_seconds}."},
    {"id": "api_telemetry", "type": "api-signature",
     "fact": "Fleet telemetry is ingested via the gRPC method TelemetryService.Report(stream TelemetryPoint) returning ReportAck{accepted_count}. It is a client-streaming RPC.",
     "question": "How is fleet telemetry ingested at the API level?",
     "answer": "Via the client-streaming gRPC method TelemetryService.Report(stream TelemetryPoint) returning ReportAck{accepted_count}."},

    # --- bug-fix (episodic) ---
    {"id": "bug_tz", "type": "bug-fix",
     "fact": "We hit a bug where invoice due-dates were off by a day for customers in UTC+ timezones. Root cause: due dates were computed with time.Now() in server-local time. Fix: compute in UTC and store with timezone, in PR #482.",
     "question": "What caused the invoice due-date off-by-one-day bug and how was it fixed?",
     "answer": "Due dates were computed in server-local time via time.Now(); fixed (PR #482) by computing in UTC and storing with timezone."},
    {"id": "bug_nats_redeliver", "type": "bug-fix",
     "fact": "We had duplicate shipment notifications. Root cause: the NATS consumer acked AFTER sending the email, so a slow SMTP call caused redelivery. Fix: make the email send idempotent with a dedup key and ack before side effects.",
     "question": "Why were duplicate shipment notifications happening and what was the fix?",
     "answer": "The NATS consumer acked after the slow SMTP send, causing redelivery; fixed by making email sends idempotent with a dedup key and acking before side effects."},
    {"id": "bug_redis_stampede", "type": "bug-fix",
     "fact": "Routing latency spiked under load due to a cache stampede when popular routes expired. Fix: added singleflight around route computation so only one goroutine recomputes a key while others wait.",
     "question": "What caused the routing latency spikes under load and how was it resolved?",
     "answer": "A cache stampede when popular routes expired; fixed by adding singleflight so only one goroutine recomputes a key while others wait."},

    # --- code-location ---
    {"id": "loc_auth_mw", "type": "code-location",
     "fact": "The JWT auth middleware lives in pkg/gateway/middleware/auth.go; the JWKS refresh logic is in pkg/gateway/middleware/jwks.go.",
     "question": "Where in the repo is the JWT auth middleware defined?",
     "answer": "pkg/gateway/middleware/auth.go (with JWKS refresh in pkg/gateway/middleware/jwks.go)."},
    {"id": "loc_migrations", "type": "code-location",
     "fact": "All SQL migrations live under db/migrations/<service>/ — e.g. billing migrations are in db/migrations/billing/.",
     "question": "Where are the billing-service database migrations located?",
     "answer": "Under db/migrations/billing/ (migrations live in db/migrations/<service>/)."},
    {"id": "loc_protos", "type": "code-location",
     "fact": "Protobuf definitions are in api/proto/, and generated Go stubs are committed to gen/go/ — never edit gen/go/ by hand.",
     "question": "Where do the protobuf definitions and generated stubs live?",
     "answer": "Protos in api/proto/, generated Go stubs in gen/go/ (which must never be hand-edited)."},
]

# Knowledge-update facts: an OLD session then a NEW session; gold = newest.
UPDATES: list[dict] = [
    {"id": "ku_orders_proto", "type": "knowledge-update",
     "old_fact": "The orders-service exposes a REST/JSON API served on port 8080.",
     "new_fact": "The orders-service was migrated off REST: it now serves gRPC on port 9090, and the old REST/JSON API on 8080 is fully removed.",
     "question": "What protocol and port does the orders-service currently expose?",
     "answer": "gRPC on port 9090 (the old REST/JSON on 8080 was removed)."},
    {"id": "ku_deploy", "type": "knowledge-update",
     "old_fact": "Deployments are done by running ./scripts/deploy.sh which kubectl-applies manifests manually.",
     "new_fact": "We moved to GitOps: deployments now happen automatically via ArgoCD watching the helios-gitops repo. The manual deploy.sh script has been deleted.",
     "question": "How are deployments to production performed now?",
     "answer": "Automatically via ArgoCD watching the helios-gitops repo (GitOps); the old manual deploy.sh was deleted."},
    {"id": "ku_logging", "type": "knowledge-update",
     "old_fact": "Services log with logrus in text format.",
     "new_fact": "Logging was standardized on slog with structured JSON output; logrus was removed from all services.",
     "question": "What logging library and format do Helios services use now?",
     "answer": "slog with structured JSON output (logrus was removed)."},
    {"id": "ku_default_branch", "type": "knowledge-update",
     "old_fact": "The default branch protection requires 1 approving review to merge.",
     "new_fact": "Branch protection was tightened: merging now requires 2 approving reviews plus a green security scan.",
     "question": "How many approvals are required to merge to the default branch now?",
     "answer": "2 approving reviews plus a passing security scan."},
]

# Multi-session facts: each sub-fact gets its own session; answer combines them.
MULTI: list[dict] = [
    {"id": "ms_deploy_pipeline", "type": "multi-session",
     "facts": [
        "Container images in Helios must be signed with cosign in CI before they can be deployed.",
        "ArgoCD is configured to refuse syncing any image whose cosign signature does not verify.",
     ],
     "question": "End to end, what makes a container image eligible to be deployed to production?",
     "answer": "CI signs the image with cosign, and ArgoCD refuses to sync any image whose cosign signature doesn't verify — so only signed, verifiable images deploy."},
    {"id": "ms_invoice_flow", "type": "multi-session",
     "facts": [
        "When an order is marked delivered, the orders-service publishes an order.delivered event to NATS.",
        "The billing-service subscribes to order.delivered and generates the invoice via CreateInvoice with an idempotency key derived from the order ID.",
     ],
     "question": "How does an invoice get generated after an order is delivered?",
     "answer": "orders-service publishes order.delivered to NATS; billing-service consumes it and calls CreateInvoice with an idempotency key derived from the order ID."},
    {"id": "ms_oncall", "type": "multi-session",
     "facts": [
        "Production alerts page the on-call engineer through PagerDuty, integrated with the #helios-incidents Slack channel.",
        "The on-call runbook requires creating an incident doc from the incident-template before any mitigation is attempted.",
     ],
     "question": "What are the first steps when a production alert fires?",
     "answer": "PagerDuty pages on-call (surfaced in #helios-incidents), and the on-call must create an incident doc from the incident-template before attempting mitigation."},
]

# Abstention questions: plausible but NOT in memory; agent must decline.
ABSTAIN: list[dict] = [
    {"id": "abs_redis_version", "type": "code-architecture",
     "question": "What exact version of Redis does the routing-service run in production?",
     "answer": "I don't have enough information to answer that."},
    {"id": "abs_frontend_fw", "type": "code-architecture",
     "question": "Which frontend framework does the Helios customer dashboard use?",
     "answer": "I don't have enough information to answer that."},
    {"id": "abs_db_password", "type": "code-location",
     "question": "Where is the production database password stored?",
     "answer": "I don't have enough information to answer that."},
]

# ---------------------------------------------------------------------------
# LLM rendering of a fact into a realistic dev<->agent session.
# ---------------------------------------------------------------------------
RENDER_SYSTEM = """\
You generate realistic transcripts of a software engineer (role "user") pair-working
with an AI coding agent (role "assistant") on a real codebase. The transcript must
read like a genuine working session: concrete file names, commands, code, decisions.

You will be given ONE fact that must be clearly and unambiguously established in the
conversation (stated by whichever speaker is natural). Do not contradict the fact.
Keep it 4-6 turns total, alternating user/assistant, concise and technical.

Return ONLY a JSON array of objects: [{"role":"user","content":"..."},{"role":"assistant","content":"..."}]
No prose, no markdown fences.
"""

DISTRACTOR_SYSTEM = """\
You generate a short realistic transcript (4-6 turns, alternating user/assistant) of a
software engineer pair-working with an AI coding agent on a logistics microservices
platform. Pick a MUNDANE, SELF-CONTAINED topic (a small refactor, a flaky test, a
dependency bump, a config tweak, a log message, a typo fix). It must NOT establish any
durable architecture decision, convention, API signature, bug-fix conclusion, or file
location that could be asked about later — just routine chatter.

Return ONLY a JSON array: [{"role":"user","content":"..."},{"role":"assistant","content":"..."}]
No prose, no markdown fences.
"""


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s.rsplit("```", 1)[0]
    return s.strip()


async def render_session(fact_text: str, model: str, sem: asyncio.Semaphore,
                         system: str = RENDER_SYSTEM, user_extra: str = "") -> list[dict]:
    user = (user_extra or f"Project context: {PROJECT}\n\nFact to establish:\n{fact_text}")
    async with sem:
        for attempt in range(3):
            try:
                resp = await _client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    max_tokens=1200,
                )
                raw = _strip_fences(resp.choices[0].message.content or "")
                turns = json.loads(raw)
                if isinstance(turns, list) and turns and all(
                    isinstance(t, dict) and "role" in t and "content" in t for t in turns
                ):
                    return turns
            except Exception:
                await asyncio.sleep(0.6 * (attempt + 1))
    # Fallback: a minimal deterministic session so generation never blocks.
    return [{"role": "user", "content": "Note for the record:"},
            {"role": "assistant", "content": fact_text}]


# ---------------------------------------------------------------------------
# Build dataset
# ---------------------------------------------------------------------------
async def build(model: str, n_distractors: int, out_path: Path) -> None:
    cache_path = BENCHMARK_DIR / "codemem_sessions_cache.json"
    cache: dict = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    sem = asyncio.Semaphore(8)

    # Collect every (cache_key, fact_text, system, user_extra) we must render.
    render_jobs: list[tuple[str, str, str, str]] = []

    def need(key: str, fact_text: str, system=RENDER_SYSTEM, user_extra=""):
        if key not in cache:
            render_jobs.append((key, fact_text, system, user_extra))

    for f in FACTS:
        need(f["id"], f["fact"])
    for u in UPDATES:
        need(u["id"] + "__old", u["old_fact"])
        need(u["id"] + "__new", u["new_fact"])
    for m in MULTI:
        for i, sub in enumerate(m["facts"]):
            need(f"{m['id']}__{i}", sub)
    for i in range(n_distractors):
        # vary the prompt slightly by index so sessions differ
        need(f"distractor_{i}", "", DISTRACTOR_SYSTEM,
             f"{DISTRACTOR_SYSTEM}\nVariation seed: {i}. Project: {PROJECT}")

    print(f"Rendering {len(render_jobs)} sessions ({len(cache)} cached) with {model}...")

    async def run_job(job):
        key, fact_text, system, user_extra = job
        turns = await render_session(fact_text, model, sem, system, user_extra)
        return key, turns

    done = 0
    tasks = [asyncio.create_task(run_job(j)) for j in render_jobs]
    for coro in asyncio.as_completed(tasks):
        key, turns = await coro
        cache[key] = turns
        done += 1
        if done % 10 == 0 or done == len(tasks):
            print(f"  rendered {done}/{len(tasks)}")
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))

    # Distractor sessions used to pad every haystack.
    distractors = [(f"distractor_{i}", cache[f"distractor_{i}"]) for i in range(n_distractors)]

    # Chronological dates: spread sessions across ~2024.
    def date_for(slot: int) -> str:
        # slot 0..N over the year
        from datetime import date, timedelta
        return (date(2024, 1, 8) + timedelta(days=slot * 6)).isoformat()

    questions: list[dict] = []

    def assemble(qid: str, qtype: str, question: str, answer: str,
                 evidence: list[tuple[str, list[dict]]], updated: bool = False,
                 abstain: bool = False) -> dict:
        """Build one LongMemEval-format question.

        evidence: list of (session_id, turns). For knowledge-update the OLD
        session must come chronologically BEFORE the NEW one.
        """
        n_pad = max(0, 18 - len(evidence))
        pad = RNG.sample(distractors, min(n_pad, len(distractors)))
        # Interleave evidence among distractors but preserve evidence order.
        slots = list(pad)
        # choose increasing insertion positions for evidence to keep old<new
        positions = sorted(RNG.sample(range(len(slots) + len(evidence)), len(evidence)))
        combined: list[tuple[str, list[dict]]] = []
        ei = 0
        di = 0
        for pos in range(len(slots) + len(evidence)):
            if ei < len(evidence) and pos == positions[ei]:
                combined.append(evidence[ei]); ei += 1
            elif di < len(slots):
                combined.append(slots[di]); di += 1
        # any leftovers
        while di < len(slots):
            combined.append(slots[di]); di += 1

        sids = [f"{qid}_s{i}" for i in range(len(combined))]
        sessions = [turns for _, turns in combined]
        dates = [date_for(i) for i in range(len(combined))]
        # answer session ids = the evidence ones, found by identity
        answer_ids = []
        for i, (orig_id, _) in enumerate(combined):
            if any(orig_id == ev_id for ev_id, _ in evidence):
                answer_ids.append(sids[i])
        from datetime import date as _d, timedelta as _td
        qdate = (_d(2024, 1, 8) + _td(days=(len(combined) + 1) * 6)).isoformat()
        return {
            "question_id": qid + ("_abs" if abstain else ""),
            "question_type": qtype,
            "question": question,
            "answer": answer,
            "question_date": qdate,
            "haystack_dates": dates,
            "haystack_session_ids": sids,
            "haystack_sessions": sessions,
            "answer_session_ids": answer_ids,
        }

    for f in FACTS:
        questions.append(assemble(f["id"], f["type"], f["question"], f["answer"],
                                  [(f["id"], cache[f["id"]])]))
    for u in UPDATES:
        ev = [(u["id"] + "__old", cache[u["id"] + "__old"]),
              (u["id"] + "__new", cache[u["id"] + "__new"])]
        questions.append(assemble(u["id"], u["type"], u["question"], u["answer"],
                                  ev, updated=True))
    for m in MULTI:
        ev = [(f"{m['id']}__{i}", cache[f"{m['id']}__{i}"]) for i in range(len(m["facts"]))]
        questions.append(assemble(m["id"], m["type"], m["question"], m["answer"], ev))
    for a in ABSTAIN:
        questions.append(assemble(a["id"], a["type"], a["question"], a["answer"],
                                  [], abstain=True))

    out_path.write_text(json.dumps(questions, ensure_ascii=False, indent=1))
    # Report composition
    from collections import Counter
    comp = Counter(q["question_type"] for q in questions)
    n_abs = sum(1 for q in questions if q["question_id"].endswith("_abs"))
    print(f"\nWrote {len(questions)} questions -> {out_path.name}")
    print(f"  abstention: {n_abs}")
    for t, c in sorted(comp.items()):
        print(f"  {t:<18} {c}")
    avg_h = sum(len(q["haystack_sessions"]) for q in questions) / len(questions)
    print(f"  avg haystack sessions/question: {avg_h:.1f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the CodeMemEval dataset")
    ap.add_argument("--model", default="gpt-5.5", help="LLM for rendering sessions")
    ap.add_argument("--distractors", type=int, default=40)
    ap.add_argument("--out", default=str(BENCHMARK_DIR / "codemem_dataset.json"))
    args = ap.parse_args()
    if not os.environ.get("OPENROUTER_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: no API key (set OPENROUTER_API_KEY in benchmark/.env)")
        sys.exit(1)
    asyncio.run(build(args.model, args.distractors, Path(args.out)))


if __name__ == "__main__":
    main()
