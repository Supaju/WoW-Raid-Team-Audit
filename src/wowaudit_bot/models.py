from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


GearTrack = Literal["adventurer", "veteran", "champion", "hero", "myth", "crafted"]


class Socket(BaseModel):
    filled: bool


class EquippedItem(BaseModel):
    slot: str
    item_id: int | None = None
    item_name: str | None = None
    item_level: int = 0
    track: GearTrack | None = None
    enchanted: bool = False
    sockets: list[Socket] = Field(default_factory=list)


class Character(BaseModel):
    id: int | None = None
    name: str
    realm: str
    class_name: str | None = None
    role: str | None = None
    rank: str | None = None
    item_level: float = 0.0
    mythic_plus_weekly: int = 0
    mythic_plus_avg_level: float = 0.0
    mythic_plus_highest: int = 0
    items: list[EquippedItem] = Field(default_factory=list)

    def item_by_slot(self, slot: str) -> EquippedItem | None:
        for item in self.items:
            if item.slot == slot:
                return item
        return None


class CheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"

    @classmethod
    def worst(cls, *statuses: "CheckStatus") -> "CheckStatus":
        rank = {cls.PASS: 0, cls.WARN: 1, cls.FAIL: 2}
        return max(statuses, key=lambda s: rank[s])


class MythicPlusCheck(BaseModel):
    count: int
    target: int
    status: CheckStatus


class EnchantCheck(BaseModel):
    enchanted: int
    required: int
    missing_slots: list[str]
    status: CheckStatus


class SocketCheck(BaseModel):
    filled: int
    total: int
    missing_slots: list[str]
    status: CheckStatus


class GearTierCounts(BaseModel):
    myth: int = 0
    hero: int = 0
    champion: int = 0
    crafted: int = 0
    lower: int = 0


class AmbiguousItem(BaseModel):
    slot: str
    item_name: str | None = None
    item_level: int
    classified_as: str
    could_also_be: str


class GradedCharacter(BaseModel):
    character: Character
    mythic_plus: MythicPlusCheck
    enchants: EnchantCheck
    sockets: SocketCheck
    gear: GearTierCounts
    ambiguous_items: list[AmbiguousItem] = Field(default_factory=list)
    overall_status: CheckStatus
