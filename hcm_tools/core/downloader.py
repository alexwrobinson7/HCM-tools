"""
Concurrent bulk download orchestrator.

Architecture
------------
Phase 1 — Scrape
    A single page (the main authenticated page) iterates through all listing
    pages and registers every DocumentRecord into the database.  Records that
    haven't been completed yet are pushed onto an asyncio.Queue.

Phase 2 — Download (concurrent)
    N worker coroutines drain the queue.  Each worker owns its own Playwright
    Page (created from the shared browser context, so it inherits the session
    cookies).  Workers run independently and share a RateLimiter.

Session timeout
    When a worker detects a login redirect it acquires a lock, pauses all
    workers via an asyncio.Event, prompts the user to re-authenticate in the
    browser, then releases the event so workers resume.  The failed download
    is re-queued for retry.
"""

import asyncio
import logging
import random
from typing import Tuple, Type

from playwright.async_api import BrowserContext

from .db import DownloadDB
from .rate_limiter import RateLimiter
from .retry import with_retry
from ..adapters.base import BaseAdapter, DocumentRecord

logger = logging.getLogger(__name__)


class BulkDownloader:

    def __init__(
        self,
        adapter_class: Type[BaseAdapter],
        scrape_adapter: BaseAdapter,   # pre-authenticated adapter on the main page
        context: BrowserContext,       # shared browser context for worker pages
        db: DownloadDB,
        config: dict,
    ):
        self.adapter_class  = adapter_class
        self.scrape_adapter = scrape_adapter
        self.context        = context
        self.db             = db
        self.config         = config

        dl_cfg    = config.get("download", {})
        retry_cfg = config.get("retry", {})
        rate_cfg  = config.get("rate_limit", {})
        conc_cfg  = config.get("concurrency", {})

        self.output_dir:       str   = config.get("output", {}).get("directory", "output")
        self.n_workers:        int   = conc_cfg.get("workers", 3)
        self.delay_min:        float = dl_cfg.get("delay_min", 1.0)
        self.delay_max:        float = dl_cfg.get("delay_max", 3.0)
        self.max_attempts:     int   = retry_cfg.get("max_attempts", 3)
        self.retry_base_delay: float = retry_cfg.get("base_delay", 2.0)
        self.retry_max_delay:  float = retry_cfg.get("max_delay", 60.0)

        max_per_min = rate_cfg.get("downloads_per_minute", 30)
        self.rate_limiter = RateLimiter(max_calls=max_per_min, window=60.0)

        # Event is SET while the session is healthy; workers wait on it.
        self._session_ok    = asyncio.Event()
        self._session_ok.set()
        self._reauth_lock   = asyncio.Lock()

    # ── Public entry point ─────────────────────────────────────────────────

    async def run(self, start_page: int = 1) -> Tuple[int, int, int]:
        """
        Scrape all listing pages, then download concurrently.
        Returns (downloaded, skipped, failed).
        """
        queue: asyncio.Queue[DocumentRecord] = asyncio.Queue()

        # Phase 1: scrape
        total = await self._scrape_all(queue, start_page)
        logger.info(
            f"Discovered {total} document(s). "
            f"Starting {self.n_workers} download worker(s)."
        )

        # Phase 2: concurrent download
        tasks = [
            asyncio.create_task(self._worker(i, queue))
            for i in range(self.n_workers)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        downloaded = sum(r[0] for r in results)
        skipped    = sum(r[1] for r in results)
        failed     = sum(r[2] for r in results)

        logger.info(
            f"Run complete — downloaded: {downloaded}, "
            f"skipped: {skipped}, failed: {failed}"
        )
        return downloaded, skipped, failed

    # ── Phase 1: Scraping ──────────────────────────────────────────────────

    async def _scrape_all(
        self, queue: asyncio.Queue, start_page: int
    ) -> int:
        """Page through the listing with the main adapter; populate the queue."""
        adapter = self.scrape_adapter
        await adapter.navigate_to_documents()

        # Fast-forward to start_page (for resumed runs)
        for _ in range(start_page - 1):
            if not await adapter.has_next_page():
                break
            await adapter.go_to_next_page()

        page_num = start_page
        total = 0

        while True:
            logger.info(f"Scraping listing page {page_num}…")
            await self.db.set_last_page(page_num)

            records = await adapter.get_documents_on_page(page_num)
            logger.info(f"  Found {len(records)} record(s)")

            for record in records:
                await self.db.register_document(
                    record.id, record.employee_name, record.employee_id,
                    record.doc_type, record.doc_date,
                    record.listing_page, record.row_index,
                )
                if not await self.db.is_completed(record.id):
                    await queue.put(record)
                total += 1

            if not await adapter.has_next_page():
                break
            await adapter.go_to_next_page()
            page_num += 1

        return total

    # ── Phase 2: Worker coroutines ─────────────────────────────────────────

    async def _worker(
        self, worker_id: int, queue: asyncio.Queue
    ) -> Tuple[int, int, int]:
        """
        Single download worker.  Owns its own Playwright Page + Adapter so
        it can navigate independently while sharing the session cookies.
        """
        downloaded = skipped = failed = 0
        log = logging.getLogger(f"{__name__}.w{worker_id}")

        page = await self.context.new_page()
        adapter = self.adapter_class(self.config, page)

        try:
            while True:
                try:
                    record = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                # Block here if another worker is handling re-authentication
                await self._session_ok.wait()

                # Another worker may have completed this while we were waiting
                if await self.db.is_completed(record.id):
                    log.debug(f"[SKIP] {record.id}")
                    skipped += 1
                    queue.task_done()
                    continue

                # Acquire a rate-limit slot before downloading
                await self.rate_limiter.acquire()

                await self.db.mark_in_progress(record.id)
                try:
                    path = await with_retry(
                        lambda rec=record: adapter.download_document(
                            rec, self.output_dir
                        ),
                        max_attempts=self.max_attempts,
                        base_delay=self.retry_base_delay,
                        max_delay=self.retry_max_delay,
                        label=record.id,
                    )
                    await self.db.mark_completed(record.id, path)
                    log.info(f"[OK]   {record.id} → {path}")
                    downloaded += 1

                except Exception as exc:
                    # Check whether the failure was a session timeout
                    if await adapter.is_session_expired():
                        log.warning(f"Session expired during {record.id} — triggering re-auth.")
                        await self._handle_session_timeout(record, queue)
                        queue.task_done()
                        continue

                    log.error(f"[FAIL] {record.id}: {exc}")
                    await self.db.mark_failed(record.id, str(exc))
                    failed += 1

                # Randomised inter-download jitter to avoid bursty patterns
                await asyncio.sleep(random.uniform(self.delay_min, self.delay_max))
                queue.task_done()

        finally:
            await page.close()

        return downloaded, skipped, failed

    # ── Session timeout handling ───────────────────────────────────────────

    async def _handle_session_timeout(
        self, record: DocumentRecord, queue: asyncio.Queue
    ) -> None:
        """
        Coordinate re-authentication across all workers.

        Only one worker runs the re-auth prompt at a time (guarded by
        _reauth_lock).  All other workers block on _session_ok until the
        re-auth completes.  The triggering record is re-queued so it will
        be retried once the session is restored.
        """
        async with self._reauth_lock:
            if self._session_ok.is_set():
                # We are the first worker to detect the timeout — pause others
                self._session_ok.clear()
                logger.warning("Session timeout detected — pausing all workers.")
                await self._prompt_reauth()
                await queue.put(record)
                self._session_ok.set()
                logger.info("Session restored — workers resuming.")
            else:
                # Another worker is already handling re-auth; just re-queue
                await queue.put(record)

    async def _prompt_reauth(self) -> None:
        print()
        print("!" * 60)
        print("  SESSION TIMEOUT")
        print("  Please log in again in the browser window.")
        print("  When you are back on the authenticated home page,")
        print("  press ENTER here to resume all workers.")
        print("!" * 60)
        await asyncio.get_event_loop().run_in_executor(
            None, input, "  Press ENTER to resume... "
        )
        print()
        logger.info("User confirmed session restored.")
