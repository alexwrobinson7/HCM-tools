# HCM Tools

Python RPA tool for automating bulk document downloads from enterprise HRIS portals.
Built with [Playwright](https://playwright.dev/python/).

**Current systems supported:** ADP Vantage

---

## How it works

1. A browser window opens and navigates to the HRIS login page.
2. You log in manually (including MFA / SSO).
3. You press **Enter** in the terminal to hand control back to the tool.
4. The tool pages through the document listing, clicking each download button with a randomised delay, and saves files to a structured output directory.
5. Progress is persisted — if the run is interrupted you can resume with `--resume`.

---

## Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install the package and dependencies
pip install -e .

# 3. Install Playwright browsers (one-time)
playwright install chromium
```

---

## Usage

```bash
# Basic run — opens ADP Vantage login, waits for you, then downloads
hcm-tools --system adp_vantage

# Resume an interrupted run from the last saved page
hcm-tools --system adp_vantage --resume

# Override the output directory
hcm-tools --system adp_vantage --output /path/to/docs

# Verbose logging
hcm-tools --system adp_vantage --log-level DEBUG

# Wipe state and start fresh
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
| `selectors.document_list.*` | CSS selectors for rows, download button, metadata |
| `selectors.pagination.*` | CSS selectors for next-page button |
| `download.delay_min/max` | Random delay (seconds) between downloads |
| `output.directory` | Where to save downloaded files |

---

## Adding a new HRIS system

1. Copy `config/adp_vantage.yaml` → `config/<new_system>.yaml` and update selectors.
2. Create `hcm_tools/adapters/<new_system>.py` extending `BaseAdapter`.
3. Register it in `hcm_tools/adapters/__init__.py`.

---

## Output structure

```
output/
└── adp_vantage/
    ├── EMP001_Jane_Smith_W2_2024.pdf
    ├── EMP001_Jane_Smith_PayStub_2024-03-15.pdf
    └── ...
```

---

## Project layout

```
HCM-tools/
├── config/
│   ├── adp_vantage.yaml      # ADP Vantage selectors & URLs
│   └── settings.yaml         # Global defaults
├── hcm_tools/
│   ├── main.py               # CLI entry point
│   ├── core/
│   │   ├── browser.py        # Playwright session + manual-login pause
│   │   ├── downloader.py     # Pagination loop & download orchestration
│   │   └── state.py          # JSON-backed resume state
│   └── adapters/
│       ├── base.py           # Abstract BaseAdapter + DocumentRecord
│       └── adp_vantage.py    # ADP Vantage implementation
├── output/                   # Downloaded files (git-ignored)
├── logs/                     # Log files + state JSON (git-ignored)
└── pyproject.toml
```
