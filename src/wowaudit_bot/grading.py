from __future__ import annotations

from .config import Config
from .models import (
    AmbiguousItem,
    Character,
    CheckStatus,
    EnchantCheck,
    GearTierCounts,
    GradedCharacter,
    MythicPlusCheck,
    SocketCheck,
)


def _mythic_plus_status(count: int, thresholds) -> CheckStatus:
    if count < thresholds.fail_below:
        return CheckStatus.FAIL
    if count < thresholds.warn_below:
        return CheckStatus.WARN
    return CheckStatus.PASS


def _missing_count_status(missing: int, warn_at: int, fail_at: int) -> CheckStatus:
    if missing >= fail_at:
        return CheckStatus.FAIL
    if missing >= warn_at:
        return CheckStatus.WARN
    return CheckStatus.PASS


def _grade_mythic_plus(character: Character, config: Config) -> MythicPlusCheck:
    thresholds = config.checks.mythic_plus_weekly
    return MythicPlusCheck(
        count=character.mythic_plus_weekly,
        target=thresholds.target,
        status=_mythic_plus_status(character.mythic_plus_weekly, thresholds),
    )


def _grade_enchants(character: Character, config: Config) -> EnchantCheck:
    required = config.checks.enchants.required_slots
    missing: list[str] = []
    enchanted = 0

    for slot in required:
        item = character.item_by_slot(slot)
        if item is None:
            # No item equipped in a required slot — treat as missing.
            missing.append(slot)
            continue
        if item.enchanted:
            enchanted += 1
        else:
            missing.append(slot)

    # Two-handed case: if main_hand is a 2H weapon, off_hand will be empty.
    # Drop off_hand from the requirement when no off_hand item exists.
    effective_required = len(required)
    if "off_hand" in required:
        off_hand = character.item_by_slot("off_hand")
        main_hand = character.item_by_slot("main_hand")
        if off_hand is None and main_hand is not None:
            effective_required -= 1
            if "off_hand" in missing:
                missing.remove("off_hand")

    status = _missing_count_status(
        missing=len(missing),
        warn_at=config.checks.enchants.warn_missing,
        fail_at=config.checks.enchants.fail_missing,
    )

    return EnchantCheck(
        enchanted=enchanted,
        required=effective_required,
        missing_slots=missing,
        status=status,
    )


def _grade_sockets(character: Character, config: Config) -> SocketCheck:
    auto = set(config.checks.sockets.auto_socket_slots)
    filled = 0
    total = 0
    missing_slots: list[str] = []

    for item in character.items:
        item_total = len(item.sockets)
        item_filled = sum(1 for s in item.sockets if s.filled)
        if item_total > 0:
            total += item_total
            filled += item_filled
            if item_filled < item_total and item.slot not in missing_slots:
                missing_slots.append(item.slot)

    # Enforce auto-socket slots even if the item reports no socket data
    # (defensive — covers API quirks where the socket field is omitted).
    for slot in auto:
        item = character.item_by_slot(slot)
        if item is None:
            total += 1
            if slot not in missing_slots:
                missing_slots.append(slot)
            continue
        if len(item.sockets) == 0:
            total += 1
            if slot not in missing_slots:
                missing_slots.append(slot)

    missing = total - filled
    status = _missing_count_status(
        missing=missing,
        warn_at=config.checks.sockets.warn_missing,
        fail_at=config.checks.sockets.fail_missing,
    )

    return SocketCheck(
        filled=filled,
        total=total,
        missing_slots=missing_slots,
        status=status,
    )


_COSMETIC_SLOTS = {"shirt", "tabard", "ranged"}


def _ilvl_to_tier(ilvl: int, config: Config) -> str:
    bands = config.season.ilvl_bands
    if bands.myth[0] <= ilvl <= bands.myth[1]:
        return "myth"
    if bands.hero[0] <= ilvl <= bands.hero[1]:
        return "hero"
    if bands.champion[0] <= ilvl <= bands.champion[1]:
        return "champion"
    return "lower"


def _ambiguous_alternative(ilvl: int, classified: str, config: Config) -> str | None:
    for rng in config.season.ambiguous_ranges:
        lo, hi = rng.ilvl
        if lo <= ilvl <= hi and classified in rng.between:
            other = rng.between[0] if rng.between[1] == classified else rng.between[1]
            return other
    return None


def _grade_gear_tiers(
    character: Character, config: Config
) -> tuple[GearTierCounts, list[AmbiguousItem]]:
    counts = GearTierCounts()
    ambiguous: list[AmbiguousItem] = []
    for item in character.items:
        if item.slot in _COSMETIC_SLOTS:
            continue
        track = item.track
        if track == "myth":
            counts.myth += 1
        elif track == "hero":
            counts.hero += 1
        elif track == "champion":
            counts.champion += 1
        elif track == "crafted":
            counts.crafted += 1
        elif track in ("adventurer", "veteran"):
            counts.lower += 1
        else:
            # Source label missing — classify by ilvl band and flag ambiguity
            # if the ilvl falls in a known track-overlap zone.
            classified = _ilvl_to_tier(item.item_level, config)
            if classified == "myth":
                counts.myth += 1
            elif classified == "hero":
                counts.hero += 1
            elif classified == "champion":
                counts.champion += 1
            else:
                counts.lower += 1
            alternative = _ambiguous_alternative(item.item_level, classified, config)
            if alternative is not None:
                ambiguous.append(
                    AmbiguousItem(
                        slot=item.slot,
                        item_name=item.item_name,
                        item_level=item.item_level,
                        classified_as=classified,
                        could_also_be=alternative,
                    )
                )
    return counts, ambiguous


def grade_character(character: Character, config: Config) -> GradedCharacter:
    mp = _grade_mythic_plus(character, config)
    enchants = _grade_enchants(character, config)
    sockets = _grade_sockets(character, config)
    gear, ambiguous = _grade_gear_tiers(character, config)

    overall = CheckStatus.worst(mp.status, enchants.status, sockets.status)
    return GradedCharacter(
        character=character,
        mythic_plus=mp,
        enchants=enchants,
        sockets=sockets,
        gear=gear,
        ambiguous_items=ambiguous,
        overall_status=overall,
    )


def grade_roster(characters: list[Character], config: Config) -> list[GradedCharacter]:
    return [grade_character(c, config) for c in characters]
