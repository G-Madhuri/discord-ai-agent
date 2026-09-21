# PostgreSQL Database Architecture & Persistence Verification

This document outlines the database layer design, connection parameters, migration procedures, and SSL transport verification.

## 1. Connection Architecture & URLs

The application uses SQLAlchemy with `asyncpg` (`postgresql+asyncpg://`).

- **`DATABASE_URL`**: High-performance pooled connection string used for application HTTP requests and background services (`-pooler` host when using PgBouncer / Neon).
- **`DIRECT_URL`**: Direct connection string without PgBouncer pooling, used by Alembic migrations (`alembic/env.py`). PgBouncer transaction mode rejects Alembic transactional DDL statements, so Alembic automatically falls back to `DIRECT_URL` if configured.

### Engine Configuration (`app/db/session.py`)

SQLAlchemy engines set `connect_args={"ssl": "require"}` explicitly for PostgreSQL connections to guarantee TLS transport encryption regardless of URL query parameters or driver defaults.

---

## 2. Extensions & Schema Verification

- **`pgvector` Extension**: Initial migration (`7e65b867044d_initial_schema.py`) executes `CREATE EXTENSION IF NOT EXISTS vector` to support vector embedding stores.
- **Partial Unique Index**: `uq_assignment_active_task` enforces one active/proposed assignment per task using PostgreSQL partial index predicates (`postgresql_where=sa.text("status IN ('proposed', 'active')")`).
- **Enum Lowercase Persistence**: StrEnum models map members to lowercase string values (`'active'`, `'deterministic'`) via `values_callable` in `enum_column()`, matching raw SQL index predicates.
- **Timezone Awareness**: All timestamp columns are stored with explicit timezone info (`sa.DateTime(timezone=True)`).

---

## 3. Authoritative SSL Verification Strategy

On Neon and proxy-terminated PostgreSQL setups (e.g. Cloud SQL Auth Proxy, PgBouncer), TLS is terminated at the proxy edge before passing data to the local PostgreSQL backend process socket.

> [!WARNING]
> `SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()` always returns `ssl=false` on proxy-terminated PostgreSQL instances because the internal socket from proxy to backend is unencrypted IPC. This is expected and is **not** a valid SSL indicator.

### Client-Side Authoritative Verification (Three-Check Strategy)

1. **Negative Check (Rejection of Insecure Connections)**:
   Connecting with `connect_args={"ssl": False}` against Neon PostgreSQL fails immediately with:
   `InvalidAuthorizationSpecificationError: connection is insecure (try using sslmode=require)`
2. **Positive Check (Client Transport Inspection)**:
   Inspecting `transport.get_extra_info("ssl_object")` on an active `asyncpg` connection returns an active `SSLObject` with cipher:
   `('TLS_AES_256_GCM_SHA384', 'TLSv1.3', 256)`
3. **Permanent Regression Test**:
   The `test_insecure_ssl_connection_rejection` test in `backend/tests/test_postgres_constraints.py` enforces that any attempt to disable SSL transport fails loudly.

---

## 4. Operational Scripts & Tests

- **`backend/tests/test_postgres_constraints.py`**: Pytest regression suite verifying partial indexes, FK cascade deletions, timezone retention, JSONB structures, lowercase enums, pgvector RAG store, and SSL rejection.
- **`backend/scripts/verify_postgres.py`**: One-off environment and schema sanity script for verifying database status.
