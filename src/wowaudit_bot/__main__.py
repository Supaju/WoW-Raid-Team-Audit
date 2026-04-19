from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .blizzard_client import enrich_characters_with_equipment
from .config import Config, load_config
from .grading import grade_roster
from .models import Character
from .reporting import write_snapshot
from .wowaudit_client import (
    apply_weekly_mplus,
    fetch_current_period,
    fetch_roster,
    fetch_team_info,
    fetch_weekly_mplus,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wowaudit-check",
        description="Generate a raid-readiness dashboard from wowaudit + Blizzard data.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to config.yaml (default: ./config.yaml)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("snapshots"),
        help="Directory where weekly snapshots are written (default: ./snapshots)",
    )
    parser.add_argument(
        "--skip-blizzard",
        action="store_true",
        help="Skip Blizzard calls; only populate roster + weekly M+ from wowaudit. "
        "Useful for validating wowaudit access without Blizzard credentials.",
    )
    parser.add_argument(
        "--dump-dir",
        type=Path,
        help="Write raw responses (wowaudit characters/period/historical, Blizzard "
        "equipment per character) under this directory for inspection.",
    )
    return parser.parse_args(argv)


def _dump(dir_: Path | None, name: str, payload) -> None:
    if dir_ is None:
        return
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_characters(
    config: Config, dump_dir: Path | None
) -> tuple[list[Character], dict | None, date | None]:
    roster = fetch_roster(config)
    _dump(dump_dir, "wowaudit_characters.json", [c.model_dump(mode="json") for c in roster])

    team = fetch_team_info(config)
    _dump(dump_dir, "wowaudit_team.json", team)

    period_info = fetch_current_period(config)
    _dump(dump_dir, "wowaudit_period.json", period_info)
    period = int(period_info["current_period"])
    season_start = None
    raw_start = (period_info.get("current_season") or {}).get("start_date")
    if raw_start:
        try:
            season_start = date.fromisoformat(raw_start)
        except ValueError:
            season_start = None

    counts = fetch_weekly_mplus(config, period)
    _dump(dump_dir, "wowaudit_historical_data.json", counts)

    apply_weekly_mplus(roster, counts)
    return roster, team.get("last_refreshed"), season_start


def _enrich(characters: list[Character], config: Config, dump_dir: Path | None) -> None:
    errors: list[tuple[str, str]] = []

    def _on_error(c: Character, exc: Exception) -> None:
        errors.append((f"{c.name}-{c.realm}", str(exc)))

    enrich_characters_with_equipment(
        characters, config, on_error=_on_error, raw_dump_dir=dump_dir
    )
    if errors:
        print("Blizzard equipment errors:", file=sys.stderr)
        for who, what in errors:
            print(f"  {who}: {what}", file=sys.stderr)
    if dump_dir is not None:
        _dump(
            dump_dir,
            "characters_enriched.json",
            [c.model_dump(mode="json") for c in characters],
        )


def _print_summary(graded, dashboard_path: Path) -> None:
    total = len(graded)
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for g in graded:
        counts[g.overall_status.value] += 1
    print(f"Characters audited: {total}")
    print(f"  OK:   {counts['pass']}")
    print(f"  Warn: {counts['warn']}")
    print(f"  Fail: {counts['fail']}")
    print(f"Dashboard: {dashboard_path}")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _parse_args(argv)

    if not args.config.exists():
        print(f"Config file not found: {args.config}", file=sys.stderr)
        return 2

    config = load_config(args.config)

    try:
        characters, last_refreshed, season_start = _load_characters(config, args.dump_dir)
    except (RuntimeError, httpx.HTTPError) as exc:
        print(f"wowaudit fetch failed: {exc}", file=sys.stderr)
        return 2

    if not characters:
        print("No tracking characters returned from wowaudit.", file=sys.stderr)
        return 1

    if not args.skip_blizzard:
        try:
            _enrich(characters, config, args.dump_dir)
        except (RuntimeError, httpx.HTTPError) as exc:
            print(f"Blizzard fetch failed: {exc}", file=sys.stderr)
            return 2

    graded = grade_roster(characters, config)

    dashboard_path = write_snapshot(
        graded,
        config=config,
        snapshots_root=args.output,
        now=datetime.now(timezone.utc),
        last_refreshed=last_refreshed,
        season_start=season_start,
    )
    _print_summary(graded, dashboard_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
