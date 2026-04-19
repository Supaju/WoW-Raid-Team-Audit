# wowaudit-simple-bot

A CLI that combines [wowaudit](https://wowaudit.com/) roster data with Blizzard's Battle.net API to generate a weekly raid-readiness dashboard for your WoW guild. Each run writes a static HTML snapshot under `snapshots/<raid-week>/` so you can review past weeks.

## What it checks

- **Mythic+ weekly runs** — pulled from wowaudit (`historical_data.dungeons_done`). Graded against a configurable target (default 8).
- **Gear by tier** — Champion / Hero / Myth counts. Parsed from each equipped item's track, with an item-level-band fallback.
- **Enchants** — required slots for Midnight S1: head, shoulders, chest, legs, feet, both rings, main-hand (+ off-hand if not using a 2H).
- **Sockets** — filled vs. total, including the default-socket slots (neck, both rings).

Each character is graded `OK` / `WARN` / `FAIL`. Thresholds live in `config.yaml`.

## Data sources

Two APIs, each gives us part of what we need:

| Source | Used for |
|---|---|
| **wowaudit** `https://wowaudit.com/v1/*` (Bearer auth) | Roster + weekly M+ run counts |
| **Blizzard Battle.net** (OAuth2 client credentials) | Per-slot equipped items with enchants, gems/sockets, and gear track |

The wowaudit API alone doesn't expose enchants, gems, or gear track — hence the Blizzard call per character.

## Setup

### 1. Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Get your wowaudit team API key

On your team's settings page on wowaudit.com (you must be a team admin):

```
https://wowaudit.com/<region>/<realm>/<team-slug>/main/settings
```

There's an API section with a copy button. Copy the key.

### 3. Create a Blizzard API client

1. Go to <https://develop.battle.net/access/clients> (log in with your Battle.net account).
2. Click **Create Client**.
3. Fill in:
   - **Client Name**: anything, e.g. `wowaudit-simple-bot`
   - **Redirect URLs**: leave blank (we use client_credentials, no redirect)
   - **Service URL**: blank or your GitHub repo
4. Submit. The dashboard will show your **Client ID** and **Client Secret** — copy both.

### 4. Wire secrets into `.env`

```bash
cp .env.example .env
```

Edit `.env`:
```
WOWAUDIT_API_KEY=<your wowaudit team key>
BLIZZARD_CLIENT_ID=<your blizzard client id>
BLIZZARD_CLIENT_SECRET=<your blizzard client secret>
```

### 5. Point `config.yaml` at your team

Defaults are already set for Personality Hires / us-zuljin. If you're on a different team, update `wowaudit.team_id`, `region`, and `realm` (all informational — auth by API key).

## Usage

```bash
# Full run: fetches wowaudit roster + M+ counts, then Blizzard equipment per character.
wowaudit-check

# Skip Blizzard (no gear data, just roster + M+ — handy for verifying wowaudit access):
wowaudit-check --skip-blizzard

# Dump every raw API response for inspection:
wowaudit-check --dump-dir fixtures/dump
```

Output tree:

```
snapshots/
├── index.html             ← landing page with week selector
└── 2026-W16/
    ├── dashboard.html
    └── data.json
```

Open `snapshots/index.html` in a browser. The week dropdown switches between any archived weeks.

## Configuration

All tunable rules live in `config.yaml`:

- `checks.mythic_plus_weekly.target` — how many M+ runs count as complete (default 8).
- `checks.enchants.required_slots` — which slots must be enchanted.
- `checks.sockets.auto_socket_slots` — slots that always have a socket (neck + both rings in Midnight S1).
- `checks.*.warn_missing` / `fail_missing` — how many misses trigger warn or fail.
- `season.ilvl_bands` — fallback tier thresholds when an item's track can't be read from the Blizzard payload.
- `reset.region` — `us` or `eu`. Determines when a new raid week starts.

## Project layout

```
src/wowaudit_bot/
├── __main__.py             ← CLI entrypoint
├── config.py               ← pydantic config models + YAML loader
├── models.py               ← Character, EquippedItem, GradedCharacter, ...
├── wowaudit_client.py      ← roster + weekly M+ (Bearer auth)
├── blizzard_client.py      ← OAuth2 + /equipment fetcher + response parser
├── grading.py              ← pass/warn/fail rules
├── reporting.py            ← week-key calc + Jinja render + snapshot writer
└── templates/
    ├── dashboard.html.j2
    └── index.html.j2
```

## Troubleshooting

- **`Blizzard API credentials missing`** — create a client at <https://develop.battle.net/access/clients> and put the ID/secret in `.env`.
- **`Blizzard equipment errors: <character>: not found (404)`** — the character's name or realm doesn't match Blizzard's records. Check capitalization/spelling in wowaudit; realm slugs auto-convert (e.g., `Area 52` → `area-52`).
- **All characters show `FAIL`** — either you ran with `--skip-blizzard` (no gear data), or the Blizzard calls failed silently. Inspect `fixtures/dump/characters_enriched.json` to see what was collected.
- **Gear track shows wrong counts** — the parser reads track from `name_description.display_string`. If Blizzard doesn't emit that for Midnight S1 items, it falls back to ilvl bands. Adjust `season.ilvl_bands` in config.yaml if needed.
