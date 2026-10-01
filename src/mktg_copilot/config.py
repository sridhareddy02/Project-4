"""Paths and the fixed dates of the synthetic dataset."""
from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(os.environ.get("COPILOT_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("COPILOT_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "warehouse.db"
EVENTS_PATH = DATA_DIR / "events.json"
REPORTS_DIR = ROOT / "reports"

SEED = 7
AS_OF = date(2026, 9, 27)          # last day with data (a Sunday); "today" is the next day
START = AS_OF - timedelta(days=364)  # 365 days of history, 2025-09-28 .. 2026-09-27
TODAY = AS_OF + timedelta(days=1)    # questions are asked "on" this day
