from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class WowauditConfig(BaseModel):
    # The API key identifies a specific team; team_id is informational only.
    team_id: int | str | None = None
    api_key_env: str = "WOWAUDIT_API_KEY"
    base_url: str | None = None
    region: str | None = None
    realm: str | None = None

    def resolve_api_key(self) -> str:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(
                f"Environment variable {self.api_key_env} is not set. "
                f"Put your wowaudit team API key there (e.g. in a .env file)."
            )
        return key


class BlizzardConfig(BaseModel):
    region: Literal["us", "eu", "kr", "tw"] = "us"
    locale: str = "en_US"
    client_id_env: str = "BLIZZARD_CLIENT_ID"
    client_secret_env: str = "BLIZZARD_CLIENT_SECRET"

    def resolve_credentials(self) -> tuple[str, str]:
        cid = os.environ.get(self.client_id_env)
        secret = os.environ.get(self.client_secret_env)
        if not cid or not secret:
            raise RuntimeError(
                f"Blizzard API credentials missing. Set {self.client_id_env} "
                f"and {self.client_secret_env} in your .env. "
                f"Create them at https://develop.battle.net/access/clients."
            )
        return cid, secret


class IlvlBands(BaseModel):
    champion: tuple[int, int]
    hero: tuple[int, int]
    myth: tuple[int, int]


class AmbiguousRange(BaseModel):
    ilvl: tuple[int, int]
    between: tuple[str, str]


class TrackFloors(BaseModel):
    """Real minimum ilvl an item needs to legitimately belong to a track this
    season. Used to reject stale source labels on old-expansion gear."""
    champion: int
    hero: int
    myth: int


class SeasonConfig(BaseModel):
    name: str
    ilvl_bands: IlvlBands
    track_floors: TrackFloors
    ambiguous_ranges: list[AmbiguousRange] = Field(default_factory=list)


class MythicPlusThresholds(BaseModel):
    target: int = 8
    warn_below: int = 8
    fail_below: int = 4


class EnchantThresholds(BaseModel):
    required_slots: list[str]
    warn_missing: int = 1
    fail_missing: int = 2


class SocketThresholds(BaseModel):
    auto_socket_slots: list[str]
    warn_missing: int = 1
    fail_missing: int = 2


class Checks(BaseModel):
    mythic_plus_weekly: MythicPlusThresholds
    enchants: EnchantThresholds
    sockets: SocketThresholds


class ResetConfig(BaseModel):
    region: Literal["us", "eu"] = "us"


class Config(BaseModel):
    wowaudit: WowauditConfig
    blizzard: BlizzardConfig = Field(default_factory=BlizzardConfig)
    season: SeasonConfig
    checks: Checks
    reset: ResetConfig = Field(default_factory=ResetConfig)

    @field_validator("checks")
    @classmethod
    def _lowercase_slot_names(cls, checks: Checks) -> Checks:
        checks.enchants.required_slots = [s.lower() for s in checks.enchants.required_slots]
        checks.sockets.auto_socket_slots = [s.lower() for s in checks.sockets.auto_socket_slots]
        return checks


def load_config(path: Path) -> Config:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Config.model_validate(raw)
