"""CLI entry point for HCM Tools."""

import asyncio
import logging
import sys
from pathlib import Path

import click
import yaml

from .core.browser import BrowserSession
from .core.db import DownloadDB
from .core.downloader import BulkDownloader
from .core.reporter import generate_report, print_summary
from .adapters import REGISTRY


def _load_config(system: str, config_path: str | None) -> dict:
    if config_path is None:
        config_path = f"config/{system}.yaml"
    path = Path(config_path)
    if not path.exists():
        click.echo(f"Config file not found: {path}", err=True)
        sys.exit(1)
    with path.open() as fh:
        return yaml.safe_load(fh)


def _setup_logging(log_dir: str, level: str) -> None:
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    numeric = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s"
    logging.basicConfig(
        level=numeric,
        format=fmt,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path / "hcm_tools.log"),
        ],
    )


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--system", "-s",
    required=True,
    type=click.Choice(sorted(REGISTRY.keys())),
    help="HRIS system to target.",
)
@click.option(
    "--config", "-c",
    default=None,
    metavar="FILE",
    help="Path to system config YAML (default: config/<system>.yaml).",
)
@click.option(
    "--output", "-o",
    default=None,
    metavar="DIR",
    help="Override the output directory from config.",
)
@click.option(
    "--workers", "-w",
    default=None,
    type=int,
    metavar="N",
    help="Number of concurrent download workers (overrides config).",
)
@click.option(
    "--resume",
    is_flag=True,
    help="Resume from the last page saved in the database.",
)
@click.option(
    "--reset-state",
    is_flag=True,
    help="Wipe the database and start a completely fresh run.",
)
@click.option(
    "--log-dir",
    default="logs",
    show_default=True,
    help="Directory for log files and the SQLite database.",
)
@click.option(
    "--log-level",
    default="INFO",
    show_default=True,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
)
def cli(system, config, output, workers, resume, reset_state, log_dir, log_level):
    """
    HCM Tools — concurrent bulk document downloader for enterprise HRIS portals.

    \b
    Examples:
        hcm-tools --system adp_vantage
        hcm-tools --system adp_vantage --resume
        hcm-tools --system adp_vantage --workers 5 --output /tmp/docs
        hcm-tools --system adp_vantage --reset-state --log-level DEBUG
    """
    _setup_logging(log_dir, log_level)
    asyncio.run(_run(system, config, output, workers, resume, reset_state, log_dir))


async def _run(
    system: str,
    config_path: str | None,
    output_override: str | None,
    workers_override: int | None,
    resume: bool,
    reset_state: bool,
    log_dir: str,
) -> None:
    logger = logging.getLogger(__name__)

    config = _load_config(system, config_path)

    if output_override:
        config.setdefault("output", {})["directory"] = output_override
    if workers_override is not None:
        config.setdefault("concurrency", {})["workers"] = workers_override

    db_path = str(Path(log_dir) / f"{system}.db")
    db = DownloadDB(db_path)
    await db.open()

    try:
        if reset_state:
            await db.reset()
            logger.info("Database state reset — starting fresh.")

        start_page = (await db.get_last_page()) if resume else 1
        if resume:
            logger.info(f"Resuming from listing page {start_page}")

        browser_cfg = config.get("browser", {})

        async with BrowserSession(
            headless=browser_cfg.get("headless", False),
            slow_mo=browser_cfg.get("slow_mo", 50),
            viewport=browser_cfg.get("viewport"),
        ) as session:
            AdapterClass = REGISTRY[system]

            # The scraping adapter uses the main (login) page
            scrape_adapter = AdapterClass(config, session.page)

            # Navigate to login
            login_url = config.get("login_url") or config["base_url"]
            await session.navigate(login_url)

            # Wait for human authentication
            await session.pause_for_login()

            # Hand off to the concurrent downloader
            downloader = BulkDownloader(
                adapter_class=AdapterClass,
                scrape_adapter=scrape_adapter,
                context=session.context,
                db=db,
                config=config,
            )
            await downloader.run(start_page=start_page)

        # Generate and print the summary report
        output_dir = config.get("output", {}).get("directory", "output")
        summary = await generate_report(db, output_dir, system)
        print_summary(summary)

    finally:
        await db.close()


if __name__ == "__main__":
    cli()
