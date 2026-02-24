"""Bulk download orchestrator — drives the adapter through pages and files."""

import asyncio
import logging
import random
from typing import Tuple

from .state import DownloadState
from ..adapters.base import BaseAdapter

logger = logging.getLogger(__name__)


class BulkDownloader:
    """
    Iterates pages of documents via the adapter, downloads each file,
    and persists progress so runs can be resumed.
    """

    def __init__(self, adapter: BaseAdapter, state: DownloadState, config: dict):
        self.adapter = adapter
        self.state = state

        dl_cfg = config.get("download", {})
        self.delay_min: float = dl_cfg.get("delay_min", 2.0)
        self.delay_max: float = dl_cfg.get("delay_max", 4.5)
        self.output_dir: str = config.get("output", {}).get("directory", "output")

    async def run(self, start_page: int = 1) -> Tuple[int, int, int]:
        """
        Download all documents starting from *start_page*.

        Returns
        -------
        (downloaded, skipped, failed) counts
        """
        downloaded = skipped = failed = 0
        page_num = start_page

        logger.info(f"Bulk download starting at page {page_num}")

        while True:
            logger.info(f"── Page {page_num} ──────────────────────────")
            self.state.set_last_page(page_num)

            records = await self.adapter.get_documents_on_page()
            logger.info(f"Found {len(records)} document(s) on page {page_num}")

            for record in records:
                if self.state.is_completed(record.id):
                    logger.info(f"[SKIP] {record.id}")
                    skipped += 1
                    continue

                try:
                    logger.info(f"[DL]   {record.id}")
                    path = await self.adapter.download_document(record, self.output_dir)
                    self.state.mark_completed(record.id)
                    logger.info(f"       → saved: {path}")
                    downloaded += 1
                except Exception as exc:
                    logger.error(f"[FAIL] {record.id}: {exc}")
                    self.state.mark_failed(record.id, str(exc))
                    failed += 1
                    continue

                delay = random.uniform(self.delay_min, self.delay_max)
                logger.debug(f"Waiting {delay:.1f}s…")
                await asyncio.sleep(delay)

            has_next = await self.adapter.has_next_page()
            if not has_next:
                logger.info("No more pages.")
                break

            await self.adapter.go_to_next_page()
            page_num += 1

        logger.info(
            f"Done — downloaded: {downloaded}, skipped: {skipped}, failed: {failed}"
        )
        return downloaded, skipped, failed
