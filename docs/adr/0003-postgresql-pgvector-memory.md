# ADR-003: PostgreSQL + pgvector for persistent memory

- **Status:** Accepted (interfaces implemented; PostgreSQL backend not yet implemented)
- **Date:** 2026-10-27

## Context

Agents need memory at different time scales and with different access patterns: state within a
run, durable records across runs, and retrieval by meaning. We want one operationally boring
store rather than a separate vector database from day one.

## Decision

Persistent memory will be backed by **PostgreSQL with the pgvector extension**.

The architecture distinguishes three kinds of memory:

1. **Working / short-term memory** — the workflow state carried through a run (`AgentState`).
   It is not a store.
2. **Persistent long-term memory** — durable records behind `MemoryStore`
   (`put` / `get` / `search` / `delete`).
3. **Semantic memory** — `SemanticMemoryStore`, a `MemoryStore` that can also retrieve by
   similarity (`search_similar`).

Agents and nodes depend only on these interfaces and the backend-neutral types
(`MemoryRecord`, `MemoryQuery`, `ScoredMemory`). No SQL, driver, or pgvector type appears
outside the future backend module.

## Rationale

PostgreSQL gives transactions, mature operations and one system to back up; pgvector provides
adequate vector search for the expected scale and keeps records and embeddings together.
Interfaces keep that choice replaceable and let tests run without a database.

## Consequences

- v0.1 ships only `InMemoryStore`, a process-local reference implementation that is explicitly
  not durable and not semantic. No PostgreSQL code exists yet and none is presented as ready.
- Embedding generation is the backend's concern; the interface accepts plain text.

## Future considerations

- Embedder abstraction and dimension/model versioning for stored vectors.
- Schema migrations tooling; choice of driver (`psycopg` is the current candidate).
- Retention, deletion and privacy controls (relevant to the planned security work).
- Index strategy (HNSW vs IVFFlat) once real data volumes are known.
