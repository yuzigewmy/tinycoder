# ADR-001: Use a scoped, local-first hybrid memory subsystem

## Status

Accepted

## Date

2026-07-28

## Context

TinyCoder previously persisted conversation transcripts and compacted context, but it had no durable fact or preference memory. Adding memory creates several risks:

- project facts must not leak between unrelated repositories;
- user-authored instructions and retrieved memories must not gain system-prompt authority;
- automatic extraction can store incorrect, stale, conflicting, or sensitive content;
- a remote embedding service can disclose repository content;
- memory failures must not block the primary coding workflow.

The implementation also needs to remain dependency-light and work in an offline terminal environment.

## Decision

Use a local SQLite store under `~/.tinycoder/memory/memory.db` with explicit scopes (`user`, `project_shared`, `project_local`, and `session`), lifecycle states, provenance, evidence, audit history, and soft deletion.

Project identity is derived from a normalized Git remote when available and falls back to a stable resolved-path hash. Credentials are stripped before remote identity is hashed.

Retrieval combines exact-key matching, SQLite FTS5/lexical ranking, confidence and scope weighting, and an optional embedding channel. The built-in embedding provider is dependency-free and local. External providers must declare themselves external and are rejected unless `externalEmbeddingAllowed` is enabled.

`CLAUDE.md`, rules files, memory indexes, and retrieved memory are injected as synthetic user-context messages immediately before the current user message. They never become system policy and are never written back to the transcript or compacted summary.

Automatic extraction runs only after a terminal assistant response. Explicit user directives are active immediately. Implicit preferences and verified test commands enter `pending_review` in `suggest` mode. Extraction jobs are idempotent and retry at most three times. Secret-like content is rejected before persistence.

The memory subsystem is fail-open: initialization, recall, or extraction failure does not fail an Agent turn.

## Alternatives Considered

### Markdown files only, similar to a minimal Claude Code memory workflow

- Pros: transparent, versionable, editable without tooling.
- Cons: no structured conflict handling, expiry, provenance, audit, ranked retrieval, or safe concurrent writes.
- Decision: retain Markdown instruction and `MEMORY.md` compatibility as one input channel, but use SQLite for structured memory.

### External vector database

- Pros: stronger semantic search and scalable approximate-nearest-neighbor indexes.
- Cons: operational dependency, privacy boundary expansion, network failure modes, and excessive complexity for a local terminal agent.
- Decision: reject as the default. Keep an `EmbeddingProvider` interface so deployments can opt in explicitly.

### Put project instructions directly in the system prompt

- Pros: simple and gives instructions maximum weight.
- Cons: incorrectly elevates repository-controlled text to trusted policy and enables prompt-injection escalation.
- Decision: reject. Treat project and memory text as untrusted user-authored context.

### Store all automatically extracted facts as active

- Pros: immediate recall with no review workflow.
- Cons: amplifies hallucinations and stale assumptions.
- Decision: reject. Use `suggest` as the default mode and require approval for implicit candidates.

## Consequences

- SQLite gives transactional updates, WAL mode, FTS5 when available, and simple backup/rollback.
- Scope checks and project identity prevent ordinary cross-project leakage.
- Session-scoped records are additionally filtered by source session ID.
- Retrieval remains bounded by item and token budgets.
- The built-in local hash embedding is a safe fallback, not a replacement for a high-quality semantic model.
- The initial graph is deliberately small: it indexes project-to-memory-key relations and provides a stable extension contract.
- Multi-process write throughput is limited by SQLite; this is acceptable for the current single-user CLI.
- Rule-based extraction is conservative. A future model extractor can implement the same candidate interface without changing persistence or review semantics.

## Rollback

Set `memory.mode` to `off` or run `/memory mode off`. This disables reads and writes without deleting the database. The database can be backed up or removed independently after TinyCoder exits.
