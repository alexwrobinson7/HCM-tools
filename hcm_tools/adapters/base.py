"""Abstract base class that every HRIS adapter must implement."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List

from playwright.async_api import Page


@dataclass
class DocumentRecord:
    """
    Represents one downloadable document discovered on a listing page.

    *id* must be stable and unique within a system so the database can
    deduplicate across resumed runs.

    *listing_page* and *row_index* let any worker page re-locate the
    exact DOM row without relying on stored element handles (which are
    tied to a specific page object and become stale across navigations).
    """

    id: str                          # stable unique key, e.g. "EMP001_W2_2024"
    employee_name: str
    employee_id: str
    doc_type: str
    doc_date: str
    listing_page: int                # 1-based pagination page where found
    row_index: int                   # 0-based index within that page's row list
    metadata: Dict[str, Any] = field(default_factory=dict, repr=False)


class BaseAdapter(ABC):
    """
    Contract every HRIS adapter must satisfy.

    Each adapter instance is bound to ONE Playwright Page.  The downloader
    creates one adapter per worker page so workers can operate concurrently
    within the same authenticated browser context.
    """

    def __init__(self, config: dict, page: Page):
        self.config = config
        self.page = page

    # ── Navigation ────────────────────────────────────────────────────────

    @abstractmethod
    async def navigate_to_documents(self) -> None:
        """Navigate to page 1 of the document listing and wait for it."""

    @abstractmethod
    async def go_to_listing_page(self, page_num: int) -> None:
        """Navigate to a specific pagination page of the listing."""

    # ── Scraping ──────────────────────────────────────────────────────────

    @abstractmethod
    async def get_documents_on_page(self, listing_page: int) -> List[DocumentRecord]:
        """
        Return all downloadable documents visible on the current listing page.
        *listing_page* is passed in so adapters can embed it in each record.
        """

    # ── Pagination ────────────────────────────────────────────────────────

    @abstractmethod
    async def has_next_page(self) -> bool:
        """Return True if a subsequent listing page exists."""

    @abstractmethod
    async def go_to_next_page(self) -> None:
        """Click the next-page control and wait for the new page to load."""

    # ── Download ──────────────────────────────────────────────────────────

    @abstractmethod
    async def download_document(self, record: DocumentRecord, output_dir: str) -> str:
        """
        Download the document described by *record* and save it to *output_dir*.

        The adapter must navigate its own page to ``record.listing_page`` and
        locate the row at ``record.row_index`` — it must NOT rely on cached
        element handles from the scraping phase.

        Returns the absolute path of the saved file.
        """

    # ── Session health ─────────────────────────────────────────────────────

    @abstractmethod
    async def is_session_expired(self) -> bool:
        """
        Return True if the browser has been redirected to a login / SSO page,
        indicating the authenticated session has timed out.
        """
