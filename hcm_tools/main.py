"""CLI entry point for HCM Tools."""

import asyncio
import logging
import sys
from pathlib import Path

import click
import yaml

from .core.browser import BrowserSession
from .core.downloader import BulkDownloader
from .core.state import DownloadState
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
    handlers: list = [
        logging.StreamHandler(),
        logging.FileHandler(log_path / "hcm_tools.log"),
    ]
    logging.basicConfig(level=numeric, format=fmt, handlers=handlers)


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
    "--resume",
    is_flag=True,
    help="Resume from the last saved page instead of starting over.",
)
@click.option(
    "--reset-state",
    is_flag=True,
    help="Wipe saved state and start a fresh run.",
)
@click.option(
    "--log-dir",
    default="logs",
    show_default=True,
    help="Directory for log files.",
)
@click.option(
    "--log-level",
    default="INFO",
    show_default=True,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
)
def cli(system, config, output, resume, reset_state, log_dir, log_level):
    """
    HCM Tools — automated bulk document downloader for enterprise HRIS portals.

    \b
    Example:
        hcm-tools --system adp_vantage
        hcm-tools --system adp_vantage --resume
        hcm-tools --system adp_vantage --output /tmp/docs --log-level DEBUG
    """
    _setup_logging(log_dir, log_level)
    asyncio.run(_run(system, config, output, resume, reset_state, log_dir))


async def _run(
    system: str,
    config_path: str | None,
    output_override: str | None,
    resume: bool,
    reset_state: bool,
    log_dir: str,
) -> None:
    logger = logging.getLogger(__name__)

    config = _load_config(system, config_path)

    if output_override:
        config.setdefault("output", {})["directory"] = output_override

    state_file = Path(log_dir) / f"{system}_state.json"
    state = DownloadState(str(state_file), system=system)

    if reset_state:
        state.reset()
        logger.info("State reset — starting fresh.")

    start_page = state.last_page if resume else 1
    if resume:
        logger.info(f"Resuming from page {start_page}")

    browser_cfg = config.get("browser", {})

    async with BrowserSession(
        headless=browser_cfg.get("headless", False),
        slow_mo=browser_cfg.get("slow_mo", 50),
        viewport=browser_cfg.get("viewport"),
    ) as session:
        AdapterClass = REGISTRY[system]
        adapter = AdapterClass(config, session.page)

        # 1. Go to login page
        login_url = config.get("login_url") or config["base_url"]
        await session.navigate(login_url)

        # 2. Wait for human to authenticate
        await session.pause_for_login()

        # 3. Navigate to documents listing
        await adapter.navigate_to_documents()

        # 4. Run bulk download
        downloader = BulkDownloader(adapter, state, config)
        downloaded, skipped, failed = await downloader.run(start_page=start_page)

    click.echo(
        f"\nRun complete — downloaded: {downloaded}, "
        f"skipped: {skipped}, failed: {failed}"
    )
    if failed:
        click.echo(f"Check {log_dir}/{system}_state.json for failed document IDs.")


if __name__ == "__main__":
    cli()
