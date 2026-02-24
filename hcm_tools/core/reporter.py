"""Generate summary reports (JSON + CSV) after a download run."""

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict

import click

if TYPE_CHECKING:
    from .db import DownloadDB

logger = logging.getLogger(__name__)


async def generate_report(
    db: "DownloadDB",
    output_dir: str,
    system: str,
) -> Dict[str, Any]:
    """
    Write a JSON summary and (if there are failures) a CSV of failed documents.
    Returns the summary dict.
    """
    summary = await db.get_summary()

    report_dir = Path(output_dir) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{system}_{ts}"

    # JSON summary
    json_path = report_dir / f"{stem}_summary.json"
    payload = {**summary, "system": system, "generated_at": ts}
    # failed_details contains dicts — safe to serialise
    json_path.write_text(json.dumps(payload, indent=2, default=str))
    logger.info(f"Summary report  → {json_path}")

    # CSV of failures (only written when there are failures)
    if summary["failed_details"]:
        csv_path = report_dir / f"{stem}_failures.csv"
        fields = [
            "id", "employee_name", "employee_id",
            "doc_type", "doc_date", "attempts", "last_error",
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(summary["failed_details"])
        logger.info(f"Failure report  → {csv_path}")

    return summary


def print_summary(summary: Dict[str, Any]) -> None:
    """Print a human-readable summary table to the terminal."""
    total = sum(
        summary.get(k, 0)
        for k in ("completed", "failed", "in_progress", "pending")
    )

    click.echo("")
    click.echo("=" * 52)
    click.echo("  RUN SUMMARY")
    click.echo("=" * 52)
    click.echo(f"  {'Completed':<14} {summary['completed']:>6}")
    click.echo(f"  {'Failed':<14} {summary['failed']:>6}")
    click.echo(f"  {'Pending':<14} {summary.get('pending', 0):>6}")
    click.echo(f"  {'Total':<14} {total:>6}")
    click.echo("=" * 52)

    failures = summary.get("failed_details", [])
    if failures:
        click.echo(f"\n  Failed documents (first 10 of {len(failures)}):")
        for item in failures[:10]:
            click.echo(f"    • {item['id']}: {item.get('last_error', 'unknown error')}")
        if len(failures) > 10:
            click.echo(f"    … and {len(failures) - 10} more — see _failures.csv report")

    click.echo("")
