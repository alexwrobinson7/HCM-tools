"""ADP Vantage adapter.

Selector defaults in config/adp_vantage.yaml are placeholders.
Inspect your portal's DOM with DevTools and update the YAML before running.
"""

import logging
import re
from pathlib import Path
from typing import List

from playwright.async_api import Page, Download, TimeoutError as PlaywrightTimeoutError

from .base import BaseAdapter, DocumentRecord

logger = logging.getLogger(__name__)

# URL fragments that indicate the session has expired / user was redirected to login
_DEFAULT_EXPIRED_INDICATORS = ["/signin", "/login", "/sso", "auth/", "adfs/"]


class ADPVantageAdapter(BaseAdapter):
    """Adapter for ADP Vantage document portals."""

    def __init__(self, config: dict, page: Page):
        super().__init__(config, page)
        self._sel = config["selectors"]
        self._timeout: int = config.get("download", {}).get("timeout", 30_000)
        self._docs_url: str = config.get("documents_url") or config["base_url"]
        self._expired_indicators: List[str] = (
            config.get("session", {}).get("expired_indicators", _DEFAULT_EXPIRED_INDICATORS)
        )

    # ── Navigation ────────────────────────────────────────────────────────

    async def navigate_to_documents(self) -> None:
        logger.debug(f"Navigating to documents page: {self._docs_url}")
        await self.page.goto(self._docs_url)
        await self.page.wait_for_load_state("networkidle", timeout=self._timeout)

    async def go_to_listing_page(self, page_num: int) -> None:
        """Navigate from page 1 to *page_num* by clicking Next repeatedly."""
        await self.navigate_to_documents()
        for _ in range(page_num - 1):
            if not await self.has_next_page():
                logger.warning(f"Could not reach listing page {page_num} — ran out of pages.")
                break
            await self.go_to_next_page()

    # ── Scraping ──────────────────────────────────────────────────────────

    async def get_documents_on_page(self, listing_page: int) -> List[DocumentRecord]:
        sel = self._sel["document_list"]
        try:
            await self.page.wait_for_selector(sel["rows"], timeout=self._timeout)
        except PlaywrightTimeoutError:
            logger.warning("No document rows found on current listing page.")
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

                records.append(
                    DocumentRecord(
                        id=_make_id(employee_id, doc_type, doc_date),
                        employee_name=employee_name,
                        employee_id=employee_id,
                        doc_type=doc_type,
                        doc_date=doc_date,
                        listing_page=listing_page,
                        row_index=idx,
                    )
                )
            except Exception as exc:
                logger.warning(f"Row {idx}: parse error — {exc}")

        return records

    # ── Pagination ────────────────────────────────────────────────────────

    async def has_next_page(self) -> bool:
        sel = self._sel["pagination"]["has_next"]
        return await self.page.query_selector(sel) is not None

    async def go_to_next_page(self) -> None:
        sel = self._sel["pagination"]["next_button"]
        await self.page.click(sel)
        await self.page.wait_for_load_state("networkidle", timeout=self._timeout)

    # ── Download ──────────────────────────────────────────────────────────

    async def download_document(self, record: DocumentRecord, output_dir: str) -> str:
        # Navigate this worker's page to the correct listing page
        await self.go_to_listing_page(record.listing_page)

        # Check for session expiry immediately after navigation
        if await self.is_session_expired():
            raise RuntimeError("Session expired during navigation.")

        # Re-locate the row by index (no stale element handles)
        sel = self._sel["document_list"]
        rows = await self.page.query_selector_all(sel["rows"])

        if record.row_index >= len(rows):
            raise IndexError(
                f"Row {record.row_index} out of range "
                f"(page has {len(rows)} rows) for doc {record.id}"
            )

        row = rows[record.row_index]
        dl_el = await row.query_selector(sel["download_button"])
        if dl_el is None:
            raise RuntimeError(
                f"Download button not found at row {record.row_index} for {record.id}"
            )

        # Build output path
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        stem = _safe_filename(
            record.employee_id, record.employee_name,
            record.doc_type, record.doc_date,
        )

        async with self.page.expect_download(timeout=self._timeout) as dl_info:
            await dl_el.click()

        download: Download = await dl_info.value
        ext = Path(download.suggested_filename or "").suffix
        final_path = out_path / f"{stem}{ext}"
        await download.save_as(str(final_path))
        return str(final_path)

    # ── Session health ─────────────────────────────────────────────────────

    async def is_session_expired(self) -> bool:
        current_url = self.page.url.lower()
        return any(indicator in current_url for indicator in self._expired_indicators)


# ── Internal helpers ────────────────────────────────────────────────────────

async def _text(element, selector: str) -> str:
    """Return stripped inner text of a child element, or empty string."""
    el = await element.query_selector(selector)
    return (await el.inner_text()).strip() if el else ""


def _make_id(*parts: str) -> str:
    return "_".join(_slug(p) for p in parts if p)


def _slug(value: str) -> str:
    return re.sub(r"[^\w\-]", "_", value).strip("_")


def _safe_filename(*parts: str) -> str:
    return "_".join(_slug(p) for p in parts if p)
