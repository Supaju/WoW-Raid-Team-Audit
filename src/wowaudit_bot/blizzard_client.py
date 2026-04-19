"""Blizzard Battle.net API client for equipped-item details.

OAuth2 client-credentials flow:
    POST https://oauth.battle.net/token  (Basic auth, grant_type=client_credentials)

Equipment endpoint:
    GET https://{region}.api.blizzard.com/profile/wow/character/{realm-slug}/{name}/equipment
        ?namespace=profile-{region}&locale={locale}

The equipment payload's `equipped_items[]` contains per-slot item data including
`enchantments[]`, `sockets[]`, `level.value`, and sometimes `name_description` with
a track descriptor like "Hero 4/6". Missing track falls back to ilvl-band matching
against config.season.ilvl_bands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from .config import BlizzardConfig, Config
from .models import Character, EquippedItem, Socket


OAUTH_URL = "https://oauth.battle.net/token"

# Blizzard item slot → our normalized slot name (matches config.yaml keys).
_SLOT_MAP = {
    "HEAD": "head",
    "NECK": "neck",
    "SHOULDER": "shoulders",
    "BACK": "back",
    "CHEST": "chest",
    "SHIRT": "shirt",
    "TABARD": "tabard",
    "WRIST": "wrist",
    "HANDS": "hands",
    "WAIST": "waist",
    "LEGS": "legs",
    "FEET": "feet",
    "FINGER_1": "finger_1",
    "FINGER_2": "finger_2",
    "TRINKET_1": "trinket_1",
    "TRINKET_2": "trinket_2",
    "MAIN_HAND": "main_hand",
    "OFF_HAND": "off_hand",
    "RANGED": "ranged",
}

# Midnight S1 crafted tier boundaries (ilvl, track).
# 2-spark crafts reach ~285 (myth); 1-spark ~272 (hero); 0-spark ~259 (champion).
_CRAFTED_MYTH_MIN = 272
_CRAFTED_HERO_MIN = 259

# For M+ items with source "Mythic+", the vault Myth-option starts around R4.
_MPLUS_MYTH_MIN = 282


@dataclass
class TokenCache:
    token: str
    expires_at: float  # unix timestamp


def realm_slug(realm: str) -> str:
    """Convert a realm name like 'Area 52' to Blizzard's slug 'area-52'."""
    return (
        realm.strip()
        .lower()
        .replace("'", "")
        .replace("&", "and")
        .replace(" ", "-")
    )


def character_slug(name: str) -> str:
    """Blizzard character URL wants lowercase."""
    return name.strip().lower()


class BlizzardClient:
    def __init__(self, config: BlizzardConfig, *, timeout: float = 30.0):
        self.config = config
        self._client = httpx.Client(timeout=timeout)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BlizzardClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- auth ---------------------------------------------------------------

    def _fetch_token(self) -> str:
        import time

        client_id, client_secret = self.config.resolve_credentials()
        response = self._client.post(
            OAUTH_URL,
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        # Token TTL is usually 24h; refresh 5 min early.
        self._token_expires_at = time.time() + int(payload.get("expires_in", 86400)) - 300
        return self._token

    def _auth_token(self) -> str:
        import time

        if self._token and time.time() < self._token_expires_at:
            return self._token
        return self._fetch_token()

    # --- equipment ----------------------------------------------------------

    def fetch_equipment(self, realm: str, character_name: str) -> dict[str, Any] | None:
        """Return the raw /equipment payload, or None if the character is not found."""
        url = (
            f"https://{self.config.region}.api.blizzard.com"
            f"/profile/wow/character/{realm_slug(realm)}/{character_slug(character_name)}/equipment"
        )
        params = {
            "namespace": f"profile-{self.config.region}",
            "locale": self.config.locale,
        }
        headers = {"Authorization": f"Bearer {self._auth_token()}"}
        response = self._client.get(url, params=params, headers=headers)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def fetch_character_summary(self, realm: str, character_name: str) -> dict[str, Any] | None:
        """Return the /character summary (for ilvl, class, etc.). None on 404."""
        url = (
            f"https://{self.config.region}.api.blizzard.com"
            f"/profile/wow/character/{realm_slug(realm)}/{character_slug(character_name)}"
        )
        params = {
            "namespace": f"profile-{self.config.region}",
            "locale": self.config.locale,
        }
        headers = {"Authorization": f"Bearer {self._auth_token()}"}
        response = self._client.get(url, params=params, headers=headers)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()


# --- response parsing --------------------------------------------------------


def _parse_track(item: dict[str, Any]) -> str | None:
    """Map Blizzard's item source label (in name_description.display_string) to a track.

    Midnight S1 observed labels: Mythic, Heroic, Normal, Mythic+, Radiance Crafted,
    Timewarped, or empty. Source alone isn't enough — M+ gear can be Hero track (dungeon)
    or Myth track (high-key vault), so we combine source + ilvl.

    Returns None when the source is absent; the grading layer falls back to ilvl bands.
    """
    nd = item.get("name_description") or {}
    source = (nd.get("display_string") or "").strip().lower()
    ilvl = int((item.get("level") or {}).get("value") or 0)

    if not source:
        return None
    if source == "mythic":
        return "myth"
    if source == "heroic":
        return "hero"
    if source == "normal":
        return "champion"
    if source == "timewarped":
        return None  # fall through to ilvl bands (usually lower)
    if "mythic+" in source:
        return "myth" if ilvl >= _MPLUS_MYTH_MIN else "hero"
    if "crafted" in source:
        return "crafted"
    return None


def _parse_sockets(item: dict[str, Any]) -> list[Socket]:
    raw = item.get("sockets") or []
    result: list[Socket] = []
    for s in raw:
        # Blizzard marks a filled socket by presence of s["item"]; empty sockets have no item.
        filled = bool(s.get("item"))
        result.append(Socket(filled=filled))
    return result


def _parse_enchanted(item: dict[str, Any]) -> bool:
    enchantments = item.get("enchantments") or []
    return len(enchantments) > 0


def _parse_slot(item: dict[str, Any]) -> str:
    raw = (item.get("slot") or {}).get("type") or ""
    return _SLOT_MAP.get(raw, raw.lower())


def parse_equipped_item(item: dict[str, Any]) -> EquippedItem:
    return EquippedItem(
        slot=_parse_slot(item),
        item_id=(item.get("item") or {}).get("id"),
        item_name=item.get("name"),
        item_level=int((item.get("level") or {}).get("value") or 0),
        track=_parse_track(item),
        enchanted=_parse_enchanted(item),
        sockets=_parse_sockets(item),
    )


def parse_equipment_response(payload: dict[str, Any]) -> list[EquippedItem]:
    raw_items = payload.get("equipped_items") or []
    return [parse_equipped_item(i) for i in raw_items]


# --- orchestration ----------------------------------------------------------


def _apply_track_floors(items: list[EquippedItem], config: Config) -> None:
    """Clear stale source-label tracks that don't meet the season's ilvl floor."""
    floors = config.season.track_floors
    floor_map = {"myth": floors.myth, "hero": floors.hero, "champion": floors.champion}
    for it in items:
        if it.track in floor_map and it.item_level < floor_map[it.track]:
            it.track = None


def enrich_characters_with_equipment(
    characters: list[Character],
    config: Config,
    *,
    on_error: callable = None,  # type: ignore[valid-type]
    raw_dump_dir=None,
) -> list[Character]:
    """Fetch equipment for each character and populate items + item_level in place.

    If raw_dump_dir is provided, writes raw payloads to
    <dir>/blizzard_equipment/<name>-<realm>.json for each character.
    """
    from pathlib import Path

    dump_root = None
    if raw_dump_dir is not None:
        dump_root = Path(raw_dump_dir) / "blizzard_equipment"
        dump_root.mkdir(parents=True, exist_ok=True)

    with BlizzardClient(config.blizzard) as client:
        for c in characters:
            try:
                payload = client.fetch_equipment(c.realm, c.name)
            except httpx.HTTPStatusError as exc:
                if on_error:
                    on_error(c, exc)
                continue
            if payload is None:
                if on_error:
                    on_error(c, RuntimeError("not found (404)"))
                continue
            if dump_root is not None:
                import json as _json

                safe_name = f"{c.name}-{c.realm}".replace("/", "_").replace(" ", "_")
                (dump_root / f"{safe_name}.json").write_text(
                    _json.dumps(payload, indent=2), encoding="utf-8"
                )
            c.items = parse_equipment_response(payload)
            _apply_track_floors(c.items, config)
            # Equipped ilvl excludes cosmetic slots (shirt, tabard) and unused ranged.
            countable = [
                i for i in c.items if i.slot not in {"shirt", "tabard", "ranged"}
            ]
            if countable:
                c.item_level = round(
                    sum(i.item_level for i in countable) / len(countable), 1
                )
    return characters
