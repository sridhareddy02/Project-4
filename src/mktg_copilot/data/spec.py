"""The fictional brand's campaign portfolio and the events injected into the data.

Everything here is synthetic. Parameters were chosen so that blended paid ROAS lands near 3 and the
channels differ in sensible ways (brand search and retargeting are efficient, prospecting is not).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CampaignSpec:
    campaign_id: str
    campaign: str
    channel: str
    objective: str            # acquisition | retargeting | retention | demand_capture
    volume: float             # paid: daily spend in dollars; owned: daily impressions (all regions)
    cpm: float                # paid only: dollars per thousand impressions
    ctr: float
    click_to_session: float
    cvr: float                # sessions -> orders
    aov: float
    new_share: float          # share of orders from new customers
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def paid(self) -> bool:
        return self.channel in PAID_CHANNELS

    @property
    def expected_roas(self) -> float | None:
        if not self.paid:
            return None
        cpc = self.cpm / (1000 * self.ctr)
        return self.click_to_session * self.cvr * self.aov / cpc


PAID_CHANNELS = ("paid_search", "paid_social", "display")

CHANNEL_LABELS = {
    "paid_search": "Paid Search",
    "paid_social": "Paid Social",
    "display": "Display",
    "email": "Email",
    "organic_search": "Organic Search",
}

REGIONS = {  # share of volume, AOV multiplier, conversion multiplier
    "Northeast": (0.25, 1.04, 1.00),
    "Midwest": (0.20, 0.97, 0.98),
    "South": (0.32, 0.96, 1.00),
    "West": (0.23, 1.05, 1.03),
}

CAMPAIGNS: tuple[CampaignSpec, ...] = (
    # Paid search
    CampaignSpec("PS01", "Search - Brand Core", "paid_search", "demand_capture", 800, 95, 0.12, 0.96, 0.075, 82, 0.22, ("brand core", "search brand", "brand search")),
    CampaignSpec("PS02", "Search - Non-brand Category", "paid_search", "acquisition", 2600, 60, 0.05, 0.93, 0.040, 90, 0.62, ("non-brand category", "nonbrand category", "category search")),
    CampaignSpec("PS03", "Search - Competitor Terms", "paid_search", "acquisition", 700, 75, 0.04, 0.92, 0.035, 88, 0.70, ("competitor terms", "competitor", "conquesting")),
    CampaignSpec("PS04", "Search - Shopping Feed", "paid_search", "acquisition", 1900, 8, 0.010, 0.94, 0.042, 86, 0.50, ("shopping feed", "shopping")),
    # Paid social
    CampaignSpec("SO01", "Social - Prospecting Lookalike", "paid_social", "acquisition", 2800, 11, 0.011, 0.85, 0.028, 80, 0.74, ("prospecting lookalike", "lookalike")),
    CampaignSpec("SO02", "Social - Prospecting Interest", "paid_social", "acquisition", 1800, 9, 0.009, 0.84, 0.027, 76, 0.72, ("prospecting interest", "interest targeting", "interest audiences")),
    CampaignSpec("SO03", "Social - Retargeting Cart", "paid_social", "retargeting", 900, 16, 0.020, 0.90, 0.062, 92, 0.07, ("retargeting cart", "cart retargeting", "social cart")),
    CampaignSpec("SO04", "Social - Retargeting Viewers", "paid_social", "retargeting", 700, 13, 0.015, 0.88, 0.040, 84, 0.10, ("retargeting viewers", "viewers")),
    CampaignSpec("SO05", "Social - Creator UGC", "paid_social", "acquisition", 1100, 12, 0.013, 0.86, 0.026, 78, 0.60, ("creator ugc", "creator", "ugc")),
    # Display
    CampaignSpec("DI01", "Display - Programmatic Prospecting", "display", "acquisition", 1500, 4.5, 0.0045, 0.80, 0.020, 74, 0.80, ("programmatic prospecting", "programmatic")),
    CampaignSpec("DI02", "Display - Retargeting", "display", "retargeting", 1100, 6.5, 0.0075, 0.82, 0.035, 88, 0.10, ("display retargeting",)),
    CampaignSpec("DI03", "Display - Sponsored Placements", "display", "acquisition", 600, 8, 0.0060, 0.80, 0.022, 80, 0.65, ("sponsored placements", "sponsored")),
    # Email (owned)
    CampaignSpec("EM01", "Email - Welcome Series", "email", "acquisition", 6000, 0, 0.045, 0.97, 0.055, 80, 0.45, ("welcome series", "welcome")),
    CampaignSpec("EM02", "Email - Cart Abandonment", "email", "retargeting", 3500, 0, 0.075, 0.97, 0.100, 90, 0.10, ("cart abandonment", "abandoned cart", "cart abandon")),
    CampaignSpec("EM03", "Email - Weekly Newsletter", "email", "retention", 40000, 0, 0.012, 0.97, 0.018, 84, 0.05, ("weekly newsletter", "newsletter")),
    CampaignSpec("EM04", "Email - Win-back", "email", "retention", 9000, 0, 0.020, 0.97, 0.022, 75, 0.02, ("win-back", "winback", "win back")),
    CampaignSpec("EM05", "Email - Promotions", "email", "retention", 30000, 0, 0.015, 0.97, 0.025, 95, 0.08, ("email promotions", "promotions", "promo emails")),
    # Organic search (owned / earned)
    CampaignSpec("OR01", "Organic - Brand Terms", "organic_search", "demand_capture", 9000, 0, 0.22, 0.98, 0.060, 82, 0.30, ("organic brand", "brand terms")),
    CampaignSpec("OR02", "Organic - Category Terms", "organic_search", "acquisition", 45000, 0, 0.020, 0.97, 0.022, 88, 0.65, ("organic category", "category terms")),
    CampaignSpec("OR03", "Organic - Content and Blog", "organic_search", "acquisition", 50000, 0, 0.012, 0.95, 0.008, 70, 0.70, ("content and blog", "blog", "content")),
)

CAMPAIGN_BY_ID = {c.campaign_id: c for c in CAMPAIGNS}

OBJECTIVE_LABELS = {
    "acquisition": "Acquisition",
    "retargeting": "Retargeting",
    "retention": "Retention",
    "demand_capture": "Demand capture",
}


@dataclass(frozen=True)
class InjectedEvent:
    """A known disturbance added to the data. The copilot is never told about these; the evaluation
    checks whether it finds them on its own."""

    event_id: str
    kind: str
    start: str
    end: str
    channel: str | None
    campaign_id: str | None
    metrics: tuple[str, ...]      # metrics the event should show up in
    direction: str                # up | down
    description: str


EVENTS: tuple[InjectedEvent, ...] = (
    InjectedEvent("E1", "tracking_break", "2026-02-10", "2026-02-13", "paid_search", None,
                  ("orders", "revenue", "cvr", "roas"), "down",
                  "Conversion tracking breaks for paid search: orders and revenue collapse while sessions and spend continue."),
    InjectedEvent("E2", "creative_fatigue", "2026-05-04", "2026-06-21", "paid_social", "SO01",
                  ("ctr", "cvr", "roas"), "down",
                  "Creative fatigue on Social - Prospecting Lookalike: click-through and conversion rates decay over seven weeks until a refresh."),
    InjectedEvent("E3", "retail_peak", "2025-11-28", "2025-12-01", None, None,
                  ("orders", "revenue", "sessions"), "up",
                  "Black Friday weekend and Cyber Monday: conversion rate and traffic jump across channels."),
    InjectedEvent("E4", "cpm_inflation", "2026-08-03", "2026-09-27", "display", None,
                  ("cpc", "cpm", "roas", "cac"), "down",
                  "Auction pressure raises display CPMs by 35%; budgets are unchanged so impressions, clicks and orders fall."),
    InjectedEvent("E5", "budget_misconfiguration", "2026-04-09", "2026-04-09", "paid_search", "PS02",
                  ("spend",), "up",
                  "A pacing bug quadruples one day of spend on Search - Non-brand Category, with lower-quality traffic."),
    InjectedEvent("E6", "data_gap", "2026-03-17", "2026-03-18", "organic_search", None,
                  (), "down",
                  "Organic search rows are missing for two days (an ingestion failure)."),
)


def campaign_aliases() -> dict[str, tuple[str, ...]]:
    """Plain-language handles for each campaign (the full name always works too)."""
    return {c.campaign: c.aliases for c in CAMPAIGNS}
