"""wowaudit API client.

Verified endpoints (GET, Bearer auth, base https://wowaudit.com):
  /v1/team                          — team info
  /v1/characters                    — roster (no gear)
  /v1/period                        — current keystone period + season
  /v1/historical_data?period={N}    — per-character weekly M+ runs

The wowaudit API does not expose enchants, gems, or gear track. Those come
from Blizzard's equipment endpoint (see blizzard_client.py).
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import Config
from .models import Character


DEFAULT_BASE_URL = "https://wowaudit.com"


def _base_url(config: Config) -> str:
    return config.wowaudit.base_url or DEFAULT_BASE_URL


def _auth_headers(config: Config) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.wowaudit.resolve_api_key()}",
        "Accept": "application/json",
    }


def _get(config: Config, path: str, *, params: dict | None = None, timeout: float = 30.0) -> Any:
    url = f"{_base_url(config)}{path}"
    response = httpx.get(url, headers=_auth_headers(config), params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_current_period(config: Config) -> int:
    data = _get(config, "/v1/period")
    return int(data["current_period"])


def fetch_team_info(config: Config) -> dict:
    """Return the /v1/team payload. We use `last_refreshed` timestamps to surface
    wowaudit's data freshness on the dashboard."""
    return _get(config, "/v1/team")


def fetch_roster(config: Config) -> list[Character]:
    """Return roster as Character stubs (name/realm/class/role only; no gear/M+ yet)."""
    data = _get(config, "/v1/characters")
    characters: list[Character] = []
    for c in data:
        if c.get("status") != "tracking":
            continue
        characters.append(
            Character(
                id=c["id"],
                name=c["name"],
                realm=c["realm"],
                class_name=c.get("class"),
                role=c.get("role"),
                rank=c.get("rank"),
                item_level=0.0,
                mythic_plus_weekly=0,
                items=[],
            )
        )
    return characters


def fetch_weekly_mplus(config: Config, period: int) -> dict[int, dict]:
    """Return {character_id: {count, avg_level, highest_level}} for the given period."""
    data = _get(config, "/v1/historical_data", params={"period": period})
    result: dict[int, dict] = {}
    for entry in data.get("characters", []):
        cid = int(entry["id"])
        dungeons = entry.get("data", {}).get("dungeons_done") or []
        levels = [int(d.get("level", 0)) for d in dungeons if d.get("level")]
        result[cid] = {
            "count": len(dungeons),
            "avg_level": round(sum(levels) / len(levels), 1) if levels else 0.0,
            "highest_level": max(levels) if levels else 0,
        }
    return result


def apply_weekly_mplus(characters: list[Character], data: dict[int, dict]) -> None:
    """Populate Character M+ fields from the fetch_weekly_mplus result."""
    for c in characters:
        if c.id is not None and c.id in data:
            d = data[c.id]
            c.mythic_plus_weekly = d["count"]
            c.mythic_plus_avg_level = d["avg_level"]
            c.mythic_plus_highest = d["highest_level"]
