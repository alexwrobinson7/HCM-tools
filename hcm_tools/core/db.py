"""SQLite-backed download state — replaces the JSON state file."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)


class DownloadDB:
    """
    Async SQLite database with WAL mode so multiple coroutines can safely
    read and write concurrently.

    Tables
    ------
    documents  — one row per discovered document; tracks status + retry count.
    run_state  — key/value pairs for global state (e.g. last pagination page).
    """

    STATUS_PENDING     = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED   = "completed"
    STATUS_FAILED      = "failed"

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def open(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._create_tables()
        summary = await self.get_summary()
        logger.info(
            f"Database opened: {self.db_path} "
            f"({summary['completed']} completed, {summary['failed']} failed)"
        )

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def _create_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id            TEXT PRIMARY KEY,
                employee_name TEXT NOT NULL,
                employee_id   TEXT NOT NULL,
                doc_type      TEXT NOT NULL,
                doc_date      TEXT NOT NULL,
                listing_page  INTEGER NOT NULL DEFAULT 1,
                row_index     INTEGER NOT NULL DEFAULT 0,
                status        TEXT    NOT NULL DEFAULT 'pending',
                attempts      INTEGER NOT NULL DEFAULT 0,
                last_error    TEXT,
                file_path     TEXT,
                discovered_at TEXT    NOT NULL,
                completed_at  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_documents_status
                ON documents(status);

            CREATE TABLE IF NOT EXISTS run_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        await self._db.commit()

    # ── Document registration ──────────────────────────────────────────────

    async def register_document(
        self,
        doc_id: str,
        employee_name: str,
        employee_id: str,
        doc_type: str,
        doc_date: str,
        listing_page: int,
        row_index: int,
    ) -> None:
        """Insert a newly-discovered document; no-op if id already exists."""
        await self._db.execute(
            """
            INSERT OR IGNORE INTO documents
                (id, employee_name, employee_id, doc_type, doc_date,
                 listing_page, row_index, discovered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (doc_id, employee_name, employee_id, doc_type, doc_date,
             listing_page, row_index, _now()),
        )
        await self._db.commit()

    # ── Status queries ─────────────────────────────────────────────────────

    async def is_completed(self, doc_id: str) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM documents WHERE id=? AND status='completed'", (doc_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def get_attempts(self, doc_id: str) -> int:
        async with self._db.execute(
            "SELECT attempts FROM documents WHERE id=?", (doc_id,)
        ) as cur:
            row = await cur.fetchone()
            return row["attempts"] if row else 0

    # ── Status mutations ───────────────────────────────────────────────────

    async def mark_in_progress(self, doc_id: str) -> None:
        await self._db.execute(
            "UPDATE documents SET status='in_progress', attempts=attempts+1 WHERE id=?",
            (doc_id,),
        )
        await self._db.commit()

    async def mark_completed(self, doc_id: str, file_path: str) -> None:
        await self._db.execute(
            """
            UPDATE documents
               SET status='completed', file_path=?, completed_at=?, last_error=NULL
             WHERE id=?
            """,
            (file_path, _now(), doc_id),
        )
        await self._db.commit()

    async def mark_failed(self, doc_id: str, error: str) -> None:
        await self._db.execute(
            "UPDATE documents SET status='failed', last_error=? WHERE id=?",
            (error, doc_id),
        )
        await self._db.commit()

    # ── Run-level state ────────────────────────────────────────────────────

    async def get_last_page(self) -> int:
        async with self._db.execute(
            "SELECT value FROM run_state WHERE key='last_page'"
        ) as cur:
            row = await cur.fetchone()
            return int(row["value"]) if row else 1

    async def set_last_page(self, page: int) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO run_state (key, value) VALUES ('last_page', ?)",
            (str(page),),
        )
        await self._db.commit()

    async def reset(self) -> None:
        await self._db.execute("DELETE FROM documents")
        await self._db.execute("DELETE FROM run_state")
        await self._db.commit()
        logger.info("Database state reset.")

    # ── Summary ────────────────────────────────────────────────────────────

    async def get_summary(self) -> Dict[str, Any]:
        async with self._db.execute(
            "SELECT status, COUNT(*) AS n FROM documents GROUP BY status"
        ) as cur:
            counts = {row["status"]: row["n"] for row in await cur.fetchall()}

        async with self._db.execute(
            """
            SELECT id, employee_name, employee_id, doc_type, doc_date,
                   attempts, last_error
              FROM documents WHERE status='failed'
            """
        ) as cur:
            failed_details: List[Dict] = [dict(r) for r in await cur.fetchall()]

        return {
            "completed":     counts.get("completed", 0),
            "failed":        counts.get("failed", 0),
            "in_progress":   counts.get("in_progress", 0),
            "pending":       counts.get("pending", 0),
            "failed_details": failed_details,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
