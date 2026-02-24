"""Persistent download state — tracks completed/failed files for resume support."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class DownloadState:
    """
    JSON-backed state file.

    Schema:
    {
        "system": "adp_vantage",
        "started_at": "<iso8601>",
        "updated_at": "<iso8601>",
        "last_page": 1,
        "completed": ["doc_id_1", "doc_id_2", ...],
        "failed": [{"id": "...", "error": "...", "time": "..."}]
    }
    """

    def __init__(self, state_file: str, system: str = "unknown"):
        self.state_file = Path(state_file)
        self.system = system
        self._state = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if self.state_file.exists():
            try:
                with self.state_file.open() as fh:
                    data = json.load(fh)
                logger.info(
                    f"Loaded state from {self.state_file} "
                    f"({len(data.get('completed', []))} completed, "
                    f"{len(data.get('failed', []))} failed)"
                )
                return data
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(f"Could not read state file ({exc}), starting fresh.")

        return {
            "system": self.system,
            "started_at": _now(),
            "updated_at": _now(),
            "last_page": 1,
            "completed": [],
            "failed": [],
        }

    def save(self) -> None:
        self._state["updated_at"] = _now()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        with tmp.open("w") as fh:
            json.dump(self._state, fh, indent=2)
        tmp.replace(self.state_file)  # atomic write

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def last_page(self) -> int:
        return self._state.get("last_page", 1)

    def set_last_page(self, page: int) -> None:
        self._state["last_page"] = page
        self.save()

    def is_completed(self, doc_id: str) -> bool:
        return doc_id in self._state["completed"]

    def mark_completed(self, doc_id: str) -> None:
        if doc_id not in self._state["completed"]:
            self._state["completed"].append(doc_id)
        self.save()

    def mark_failed(self, doc_id: str, error: str) -> None:
        self._state["failed"].append(
            {"id": doc_id, "error": error, "time": _now()}
        )
        self.save()

    def reset(self) -> None:
        """Wipe state and start fresh (does not delete the file)."""
        self._state = {
            "system": self.system,
            "started_at": _now(),
            "updated_at": _now(),
            "last_page": 1,
            "completed": [],
            "failed": [],
        }
        self.save()
        logger.info("State reset.")

    @property
    def summary(self) -> dict:
        return {
            "completed": len(self._state["completed"]),
            "failed": len(self._state["failed"]),
            "last_page": self.last_page,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
