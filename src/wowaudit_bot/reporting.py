from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from urllib.parse import quote as url_quote

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .blizzard_client import realm_slug
from .config import Config
from .models import GradedCharacter


def class_slug(class_name: str | None) -> str:
    """Convert 'Death Knight' -> 'death-knight' for CSS class names."""
    if not class_name:
        return "unknown"
    return class_name.strip().lower().replace(" ", "-")


def role_group(role: str | None) -> str:
    """Normalize wowaudit role strings into three buckets."""
    if not role:
        return "unknown"
    r = role.lower()
    if r == "tank":
        return "tank"
    if r in ("heal", "healer"):
        return "healer"
    if r in ("ranged", "melee", "dps"):
        return "dps"
    return "unknown"


def raiderio_url(region: str, realm: str, character_name: str) -> str:
    slug = realm_slug(realm)
    return f"https://raider.io/characters/{region}/{slug}/{url_quote(character_name)}"


# Only the refresh fields that feed data we actually display. wowaudit's
# `percentiles` field tracks Warcraft Logs scraping, which we don't surface,
# so it's intentionally omitted.
_FRESHNESS_LABELS = {
    "mythic_plus": "M+ runs",
    "blizzard": "Gear & profile",
}


def _relative_age(age: timedelta) -> str:
    seconds = int(age.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        m = seconds // 60
        return f"{m}m ago"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h}h ago"
    d = seconds // 86400
    return f"{d}d ago"


def _staleness_level(age: timedelta) -> str:
    """pass | warn | fail based on age. Thresholds tuned for the wowaudit data
    refresh cadence — fresh if under 6h, stale past 24h."""
    h = age.total_seconds() / 3600
    if h >= 24:
        return "fail"
    if h >= 6:
        return "warn"
    return "pass"


def format_freshness(last_refreshed: dict | None, now: datetime) -> list[dict[str, Any]]:
    """Turn the raw /v1/team last_refreshed dict into a list of entries for the
    template. Sorted stale → fresh so the worst offender is visually first."""
    if not last_refreshed:
        return []
    entries = []
    for key, iso in last_refreshed.items():
        if key not in _FRESHNESS_LABELS:
            continue
        label = _FRESHNESS_LABELS[key]
        try:
            ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = now - ts
        entries.append(
            {
                "key": key,
                "label": label,
                "iso": ts.strftime("%Y-%m-%d %H:%M UTC"),
                "relative": _relative_age(age),
                "status": _staleness_level(age),
                "age_hours": age.total_seconds() / 3600,
            }
        )
    entries.sort(key=lambda e: -e["age_hours"])
    return entries


UTC = timezone.utc
_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Raid-week reset times (UTC):
# US: Tuesday 15:00 UTC (weekday=1)
# EU: Wednesday 07:00 UTC (weekday=2)
_RESET = {
    "us": {"weekday": 1, "hour": 15},
    "eu": {"weekday": 2, "hour": 7},
}


def current_raid_week_key(region: str, now: datetime | None = None) -> str:
    """Return the ISO-like week key (e.g. "2026-W16") for the current raid week.

    A raid week starts at the regional reset and ends at the next reset.
    """
    if now is None:
        now = datetime.now(UTC)
    reset = _RESET[region]
    # Find the most recent reset moment at-or-before `now`.
    candidate = now.replace(hour=reset["hour"], minute=0, second=0, microsecond=0)
    days_back = (now.weekday() - reset["weekday"]) % 7
    candidate = candidate - timedelta(days=days_back)
    if candidate > now:
        candidate = candidate - timedelta(days=7)
    iso_year, iso_week, _ = candidate.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["class_slug"] = class_slug
    env.filters["role_group"] = role_group
    env.globals["raiderio_url"] = raiderio_url
    return env


def render_dashboard(
    graded: list[GradedCharacter],
    *,
    week_key: str,
    generated_at: datetime,
    season_name: str,
    region: str,
    freshness: list[dict[str, Any]] | None = None,
) -> str:
    env = _jinja_env()
    template = env.get_template("dashboard.html.j2")
    return template.render(
        graded=graded,
        week_key=week_key,
        generated_at=generated_at.strftime("%Y-%m-%d %H:%M UTC"),
        season_name=season_name,
        region=region,
        freshness=freshness or [],
    )


def render_index(weeks: list[str], latest_week: str) -> str:
    env = _jinja_env()
    template = env.get_template("index.html.j2")
    return template.render(weeks=weeks, latest_week=latest_week)


def _list_week_dirs(snapshots_root: Path) -> list[str]:
    if not snapshots_root.exists():
        return []
    pattern = re.compile(r"^\d{4}-W\d{2}$")
    names = [p.name for p in snapshots_root.iterdir() if p.is_dir() and pattern.match(p.name)]
    return sorted(names, reverse=True)


def _serializable_graded(graded: Iterable[GradedCharacter]) -> list[dict]:
    return [g.model_dump(mode="json") for g in graded]


def write_snapshot(
    graded: list[GradedCharacter],
    *,
    config: Config,
    snapshots_root: Path,
    now: datetime | None = None,
    last_refreshed: dict | None = None,
) -> Path:
    """Render dashboard + data.json for the current week, then refresh index.html.

    Returns the path to the rendered dashboard.
    """
    if now is None:
        now = datetime.now(UTC)
    week_key = current_raid_week_key(config.reset.region, now)
    week_dir = snapshots_root / week_key
    week_dir.mkdir(parents=True, exist_ok=True)

    freshness = format_freshness(last_refreshed, now)
    dashboard_html = render_dashboard(
        graded,
        week_key=week_key,
        generated_at=now,
        season_name=config.season.name,
        region=config.blizzard.region,
        freshness=freshness,
    )
    dashboard_path = week_dir / "dashboard.html"
    dashboard_path.write_text(dashboard_html, encoding="utf-8")

    data_path = week_dir / "data.json"
    data_path.write_text(
        json.dumps(
            {
                "week": week_key,
                "generated_at": now.isoformat(),
                "season": config.season.name,
                "characters": _serializable_graded(graded),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    weeks = _list_week_dirs(snapshots_root)
    if week_key not in weeks:
        weeks.insert(0, week_key)
    index_html = render_index(weeks, latest_week=week_key)
    (snapshots_root / "index.html").write_text(index_html, encoding="utf-8")

    return dashboard_path
