# SJI Fire Utilities

Backend utilities for San Juan Island Fire & Rescue - ESO scraping, Microsoft Forms management, and data automation.

## Project Structure

```
utilities/
├── pyproject.toml              # Python project configuration
├── .env                        # Local credentials (git-ignored)
├── .env.example                # Credential template
│
├── config/                     # Configuration files (edit these!)
│   ├── apparatus.csv           # Vehicle/apparatus list
│   ├── personnel.json          # Personnel with ESO IDs
│   └── personnel.csv           # Personnel in CSV format
│
├── src/sjifire/                # Python source code
│   ├── eso/                    # ESO Suite integration
│   │   ├── models.py           # Data models (Personnel, Apparatus)
│   │   └── scraper.py          # Playwright web scraper
│   ├── forms/                  # Microsoft Forms integration
│   │   └── updater.py          # Form update via Power Automate
│   ├── utils/
│   │   └── config.py           # Settings management
│   └── scripts/                # CLI scripts
│       ├── scrape_personnel.py
│       ├── update_form.py
│       └── test_connection.py
│
├── docs/                       # Documentation
│   ├── power-automate-setup.md # Step-by-step Power Automate guide
│   └── ms-forms-management.md  # Forms management overview
│
└── .github/workflows/          # GitHub Actions
    └── update-ms-forms.yml     # Auto-update on config changes
```

## Quick Start

### 1. Set Up Environment

```bash
# Clone the repo
git clone https://github.com/sjifire/utilities.git
cd utilities

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"

# Install Playwright browser
playwright install chromium

# Copy and edit credentials
cp .env.example .env
# Edit .env with your credentials
```

### 2. Configure Credentials

Edit `.env` with your credentials:

```env
# ESO Suite
ESO_USERNAME=your_username
ESO_PASSWORD=your_password
ESO_AGENCY=sjifr

# Microsoft Graph (optional for now)
MS_GRAPH_TENANT_ID=...
MS_GRAPH_CLIENT_ID=...
MS_GRAPH_CLIENT_SECRET=...

# Power Automate (after creating the flow)
POWER_AUTOMATE_URL=https://prod-XX.westus.logic.azure.com/...
```

### 3. Test Connection

```bash
python -m sjifire.scripts.test_connection
```

## CLI Commands

### Scrape Personnel from ESO

```bash
# Scrape personnel from recent incidents
python -m sjifire.scripts.scrape_personnel

# Scrape more incidents
python -m sjifire.scripts.scrape_personnel --max-incidents 50

# Run with visible browser (for debugging)
python -m sjifire.scripts.scrape_personnel --no-headless
```

### Update Microsoft Form

```bash
# Dry run (show what would be sent)
python -m sjifire.scripts.update_form

# With verbose output
python -m sjifire.scripts.update_form --verbose

# Actually send to Power Automate
python -m sjifire.scripts.update_form --send
```

## Editing Configuration

### Apparatus (`config/apparatus.csv`)

Add or modify vehicles:

```csv
code,name,type,station,active
E31,Engine 31,SUPPRESSION,31,true
M31,Medic 31,EMS,31,true
NEW1,New Vehicle,OTHER,31,true
```

Set `active` to `false` to remove from dropdowns without deleting.

### Personnel (`config/personnel.json`)

Personnel are scraped from ESO. To manually add:

```json
{
  "personnel": [
    {
      "esoId": "123",
      "firstName": "John",
      "lastName": "Doe",
      "fullName": "John Doe"
    }
  ]
}
```

## Automation

When you push changes to `config/apparatus.csv` or `config/personnel.json`:

1. GitHub Actions workflow triggers
2. Parses config files
3. Sends update to Power Automate
4. Power Automate updates the Microsoft Form

### Setup Required

1. Create a Power Automate flow (see `docs/power-automate-setup.md`)
2. Add the HTTP trigger URL as a GitHub repository variable: `POWER_AUTOMATE_URL`

## Architecture

```
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│   GitHub     │      │    Power     │      │  Microsoft   │
│  Push to     │─────▶│   Automate   │─────▶│    Form      │
│  config/     │      │    Flow      │      │  (Updated)   │
└──────────────┘      └──────────────┘      └──────────────┘
       │
       │ Also runs locally
       ▼
┌──────────────┐
│   Python     │
│   Scripts    │
└──────────────┘
```

## Development

```bash
# Run tests
pytest

# Lint code
ruff check src/

# Format code
ruff format src/
```

## Documentation

- [Power Automate Setup Guide](docs/power-automate-setup.md) - Step-by-step instructions
- [Forms Management Overview](docs/ms-forms-management.md) - API options and architecture
