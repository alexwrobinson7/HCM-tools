# HCM Tools

Python RPA tool for automating bulk document downloads from enterprise HRIS portals.
Built with [Playwright](https://playwright.dev/python/).

**Current systems supported:** ADP Vantage

---

## How it works

1. A browser window opens and navigates to the HRIS login page.
2. You log in manually (including MFA / SSO).
3. You press **Enter** in the terminal to hand control back to the tool.
4. The tool scrapes all listing pages to discover every document, then runs N concurrent download workers — each with its own browser page sharing your authenticated session.
5. Progress is saved to a SQLite database after every file. If the run is interrupted, resume exactly where it left off with `--resume`.
6. If your session times out mid-run, all workers pause and prompt you to log in again — then automatically resume.
7. A summary report (JSON + CSV of any failures) is written to `output/<system>/reports/` at the end of every run.

---

## Setup

```bash
# 1. Create a virtual environment with Python 3.11+
/usr/local/bin/python3.11 -m venv .venv
source .venv/bin/activate

# 2. Install the package and all dependencies
pip install -e .

# 3. Install the Chromium browser Playwright will control (one-time)
playwright install chromium
```

---

## Usage

```bash
# Basic run — opens ADP Vantage login, waits for you, then downloads
hcm-tools --system adp_vantage

# Resume an interrupted run from the last saved page
hcm-tools --system adp_vantage --resume

# Run with 5 concurrent download workers
hcm-tools --system adp_vantage --workers 5

# Override the output directory
hcm-tools --system adp_vantage --output /path/to/docs

# Verbose logging
hcm-tools --system adp_vantage --log-level DEBUG

# Wipe the database and start a completely fresh run
hcm-tools --system adp_vantage --reset-state
```

---

## Configuration

Each HRIS system has its own YAML file in `config/`.
Update `config/adp_vantage.yaml` with the real CSS/ARIA selectors for your portal
(inspect the DOM with DevTools while logged in).

Key fields:

| Field | Description |
|---|---|
| `login_url` | Page to open first so you can log in |
| `documents_url` | Page containing the document listing |
| `selectors.document_list.*` | CSS selectors for rows, download button, and metadata fields |
| `selectors.pagination.*` | CSS selectors for the next-page button |
| `concurrency.workers` | Number of parallel download workers (default: 2) |
| `rate_limit.downloads_per_minute` | Max downloads per minute across all workers (default: 20) |
| `retry.max_attempts` | How many times to retry a failed download (default: 3) |
| `retry.base_delay` | Initial backoff delay in seconds before first retry (default: 2.0) |
| `download.delay_min/max` | Random jitter delay between downloads per worker (seconds) |
| `session.expired_indicators` | URL fragments that indicate a session timeout / login redirect |
| `output.directory` | Where to save downloaded files |

### Finding selectors with DevTools

1. Log in to your ADP Vantage portal in Chrome/Edge.
2. Navigate to the document listing page.
3. Right-click a document row → **Inspect**.
4. Hover over elements to identify CSS classes/attributes for each field.
5. Test a selector in the DevTools console before committing it:
   ```js
   document.querySelectorAll('<your-selector>')
   ```

---

## Adding a new HRIS system

1. Copy `config/adp_vantage.yaml` → `config/<new_system>.yaml` and update all selectors and URLs.
2. Create `hcm_tools/adapters/<new_system>.py` extending `BaseAdapter` (implement all abstract methods).
3. Register it in `hcm_tools/adapters/__init__.py`.

---

## Output structure

```
output/
└── adp_vantage/
    ├── EMP001_Jane_Smith_W2_2024.pdf
    ├── EMP001_Jane_Smith_PayStub_2024-03-15.pdf
    ├── ...
    └── reports/
        ├── adp_vantage_20240315T120000Z_summary.json
        └── adp_vantage_20240315T120000Z_failures.csv
logs/
└── adp_vantage.db    # SQLite database tracking all download state
```

---

## Project layout

```
HCM-tools/
├── config/
│   ├── adp_vantage.yaml      # ADP Vantage selectors, URLs, and tuning knobs
│   └── settings.yaml         # Global defaults
├── hcm_tools/
│   ├── main.py               # CLI entry point
│   ├── core/
│   │   ├── browser.py        # Playwright session + manual-login pause
│   │   ├── db.py             # SQLite state (resume, retry tracking, summaries)
│   │   ├── downloader.py     # Concurrent worker orchestration + session recovery
│   │   ├── rate_limiter.py   # Sliding-window rate limiter shared across workers
│   │   ├── reporter.py       # JSON + CSV summary report generation
│   │   └── retry.py          # Exponential backoff retry helper
│   └── adapters/
│       ├── base.py           # Abstract BaseAdapter + DocumentRecord
│       └── adp_vantage.py    # ADP Vantage implementation
├── output/                   # Downloaded files (git-ignored)
├── logs/                     # Log files + SQLite database (git-ignored)
└── pyproject.toml
```
