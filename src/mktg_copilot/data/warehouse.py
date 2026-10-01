"""Builds the SQLite warehouse and opens it read-only."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from .. import config
from .generate import build_dimensions, generate_facts
from .spec import EVENTS

SCHEMA = """
DROP VIEW IF EXISTS marketing_daily;
DROP TABLE IF EXISTS fact_daily;
DROP TABLE IF EXISTS dim_campaign;
DROP TABLE IF EXISTS dim_date;
CREATE TABLE dim_campaign (
  campaign_id TEXT PRIMARY KEY, campaign TEXT NOT NULL, channel TEXT NOT NULL, channel_label TEXT NOT NULL,
  channel_group TEXT NOT NULL CHECK (channel_group IN ('paid','owned')), objective TEXT NOT NULL, objective_label TEXT NOT NULL
);
CREATE TABLE dim_date (
  date TEXT PRIMARY KEY, week_start TEXT NOT NULL, month_start TEXT NOT NULL, dow INTEGER NOT NULL, is_weekend INTEGER NOT NULL
);
CREATE TABLE fact_daily (
  date TEXT NOT NULL REFERENCES dim_date(date),
  campaign_id TEXT NOT NULL REFERENCES dim_campaign(campaign_id),
  region TEXT NOT NULL,
  impressions INTEGER NOT NULL CHECK (impressions >= 0),
  clicks INTEGER NOT NULL CHECK (clicks >= 0),
  spend REAL NOT NULL CHECK (spend >= 0),
  sessions INTEGER NOT NULL CHECK (sessions >= 0),
  orders INTEGER NOT NULL CHECK (orders >= 0),
  revenue REAL NOT NULL CHECK (revenue >= 0),
  new_customers INTEGER NOT NULL CHECK (new_customers >= 0),
  PRIMARY KEY (date, campaign_id, region)
);
CREATE INDEX ix_fact_campaign ON fact_daily (campaign_id);
CREATE VIEW marketing_daily AS
SELECT f.date, d.week_start, d.month_start, d.dow, d.is_weekend,
       f.campaign_id, c.campaign, c.channel, c.channel_label, c.channel_group, c.objective, c.objective_label,
       f.region, f.impressions, f.clicks, f.spend, f.sessions, f.orders, f.revenue, f.new_customers
FROM fact_daily f
JOIN dim_campaign c USING (campaign_id)
JOIN dim_date d USING (date);
"""


def build_warehouse(path: Path | None = None, seed: int = config.SEED, events: bool = True) -> Path:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    facts = generate_facts(seed=seed, events=events)
    dims = build_dimensions()
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        dims["dim_campaign"].to_sql("dim_campaign", con, if_exists="append", index=False)
        dims["dim_date"].to_sql("dim_date", con, if_exists="append", index=False)
        facts.to_sql("fact_daily", con, if_exists="append", index=False)
        con.commit()
    finally:
        con.close()
    if events:
        (path.parent / "events.json").write_text(json.dumps([asdict(e) for e in EVENTS], indent=2))
    return path


def ensure_warehouse(path: Path | None = None) -> Path:
    path = Path(path or config.DB_PATH)
    if not path.exists():
        build_warehouse(path)
    return path


def connect_readonly(path: Path | None = None) -> sqlite3.Connection:
    """Read-only connection: even a bug in query generation cannot modify the warehouse."""
    path = ensure_warehouse(path)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    con.execute("PRAGMA query_only = ON")
    return con


def read_sql(con: sqlite3.Connection, sql: str, params: tuple | list = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, con, params=list(params))
