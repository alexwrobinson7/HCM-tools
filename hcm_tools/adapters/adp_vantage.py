"""ADP Vantage adapter.

Selector defaults are placeholders — inspect your specific portal's DOM
and update config/adp_vantage.yaml with the real values.
"""

import logging
import re
from pathlib import Path
from typing import List

from playwright.async_api import Page, Download, TimeoutError as PlaywrightTimeoutError

from .base import BaseAdapter, DocumentRecord

logger = logging.getLogger(__name__)


class ADPVantageAdapter(BaseAdapter):
    """Adapter for ADP Vantage document portals."""

    def __init__(self, config: dict, page: Page):
        super().__init__(config, page)
        self._sel = config["selectors"]
        self._timeout: int = config.get("download", {}).get("timeout", 30_000)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate_to_documents(self) -> None:
        url = self.config.get("documents_url") or self.config["base_url"]
        logger.info(f"Navigating to documents page: {url}")
        await self.page.goto(url)
        await self.page.wait_for_load_state("networkidle", timeout=self._timeout)

    # ------------------------------------------------------------------
    # Page scraping
    # ------------------------------------------------------------------

    async def get_documents_on_page(self) -> List[DocumentRecord]:
        sel = self._sel["document_list"]

        # Wait for at least one row to appear
        try:
            await self.page.wait_for_selector(sel["rows"], timeout=self._timeout)
        except PlaywrightTimeoutError:
            logger.warning("No document rows found on current page.")
            return []

        rows = await self.page.query_selector_all(sel["rows"])
        records: List[DocumentRecord] = []

        for idx, row in enumerate(rows):
            try:
                employee_name = await _text(row, sel["employee_name"]) or "unknown"
                employee_id   = await _text(row, sel["employee_id"])   or f"row{idx}"
                doc_type      = await _text(row, sel["doc_type"])      or "document"
                doc_date      = await _text(row, sel["doc_date"])      or ""
                dl_el         = await row.query_selector(sel["download_button"])

                if dl_el is None:
                    logger.debug(f"Row {idx}: no download button found, skipping.")
                    continue

                doc_id = _make_id(employee_id, doc_type, doc_date)
                records.append(
                    DocumentRecord(
                        id=doc_id,
                        employee_name=employee_name,
                        employee_id=employee_id,
                        doc_type=doc_type,
                        doc_date=doc_date,
                        download_element=dl_el,
                    )
                )
            except Exception as exc:
                logger.warning(f"Row {idx}: parse error — {exc}")

        logger.debug(f"Parsed {len(records)} record(s) from page.")
        return records

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    async def has_next_page(self) -> bool:
        sel = self._sel["pagination"]["has_next"]
        el = await self.page.query_selector(sel)
        return el is not None

    async def go_to_next_page(self) -> None:
        sel = self._sel["pagination"]["next_button"]
        logger.info("Navigating to next page…")
        await self.page.click(sel)
        await self.page.wait_for_load_state("networkidle", timeout=self._timeout)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    async def download_document(self, record: DocumentRecord, output_dir: str) -> str:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        stem = _safe_filename(
            record.employee_id,
            record.employee_name,
            record.doc_type,
            record.doc_date,
        )

        async with self.page.expect_download(timeout=self._timeout) as dl_info:
            await record.download_element.click()

        download: Download = await dl_info.value

        # Preserve the server-suggested extension if available
        suggested = download.suggested_filename or ""
        ext = Path(suggested).suffix  # e.g. ".pdf"

        final_path = out_path / f"{stem}{ext}"
        await download.save_as(str(final_path))
        return str(final_path)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

async def _text(element, selector: str) -> str:
    """Return stripped inner text of a child element, or empty string."""
    el = await element.query_selector(selector)
    if el is None:
        return ""
    return (await el.inner_text()).strip()


def _make_id(employee_id: str, doc_type: str, doc_date: str) -> str:
    parts = [employee_id, doc_type, doc_date]
    return "_".join(_slug(p) for p in parts if p)


def _slug(value: str) -> str:
    return re.sub(r"[^\w\-]", "_", value).strip("_")


def _safe_filename(*parts: str) -> str:
    return "_".join(_slug(p) for p in parts if p)
