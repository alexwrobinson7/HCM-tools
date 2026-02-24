"""Abstract base class that every HRIS adapter must implement."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List

from playwright.async_api import Page


@dataclass
class DocumentRecord:
    """
    Represents one downloadable document discovered on a listing page.

    *id* must be stable and unique within a system so the state tracker
    can deduplicate across resumed runs.
    """

    id: str                      # stable unique key  e.g. "EMP001_W2_2024"
    employee_name: str
    employee_id: str
    doc_type: str
    doc_date: str
    # The Playwright element handle for the download button/link.
    # Stored here so the adapter that found it can also click it.
    download_element: Any = field(repr=False)
    # Optional extra metadata (adapter-specific)
    metadata: dict = field(default_factory=dict, repr=False)


class BaseAdapter(ABC):
    """
    Contract every HRIS adapter must satisfy.

    Subclasses receive the raw YAML config dict for their system and the
    active Playwright Page.  They are responsible for:

      1. Navigating to the document listing.
      2. Extracting DocumentRecords from the current page.
      3. Reporting / advancing pagination.
      4. Performing the actual file download and returning the saved path.
    """

    def __init__(self, config: dict, page: Page):
        self.config = config
        self.page = page

    @abstractmethod
    async def navigate_to_documents(self) -> None:
        """Go to the document listing page and wait for it to be ready."""

    @abstractmethod
    async def get_documents_on_page(self) -> List[DocumentRecord]:
        """Return all downloadable documents visible on the current page."""

    @abstractmethod
    async def has_next_page(self) -> bool:
        """Return True if there is a subsequent page to navigate to."""

    @abstractmethod
    async def go_to_next_page(self) -> None:
        """Click the next-page control and wait for the new page to load."""

    @abstractmethod
    async def download_document(self, record: DocumentRecord, output_dir: str) -> str:
        """
        Trigger the download for *record* and save to *output_dir*.

        Returns the absolute path of the saved file.
        """
