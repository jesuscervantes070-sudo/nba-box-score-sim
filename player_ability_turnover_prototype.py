"""
READ-ONLY PROTOTYPE -- ball-security turnover-subtype investigation.

NOT wired into player_ability_estimation.py's ball_security attribute.
Demonstrates, on a SMALL real sample, that the NBA's own real play-by-
play text separates turnovers into real subtypes (Lost Ball, Bad Pass,
Traveling, Offensive Foul, etc.) -- confirming the conceptual
distinction the task asked about is real and obtainable, WITHOUT
building the full season-covering ingestion job that would require
(explicitly out of scope for this phase -- "avoid giant data/research
jobs"; a real full backfill needs one API call PER GAME, ~1,230 real
games/season across up to 30 seasons).

FINDING, checked directly: real turnover-subtype text is present in
nba_api's playbyplayv3 endpoint across the ENTIRE cached range this
project uses (confirmed on both a 2023-24 game and a real 1996-97
game) -- NOT gated behind a modern camera-tracking floor the way rim/
perimeter defense or player-tracking touches/drives are. The real
constraint is volume (one call per game), not era coverage.

CONCEPTUAL SEPARATION (per the task's own definition of "ball
security" = preservation of the ball while handling/dribbling):
  HANDLING ERRORS (numerator for a future real ball-security metric):
    Lost Ball, Traveling, Double Dribble/Carry -- real live-ball
    fumbles/violations while the player himself has the ball.
  EXCLUDED (belongs to Passing/Decision-Making or elsewhere, NOT here):
    Bad Pass -- a passing decision/execution error, not a handling one.
    Offensive Foul, Illegal Screen -- contact/positioning infractions,
    not ball-handling errors.
    Team/shot-clock/other violations -- not attributable to one
    player's handling at all.
"""
from typing import Dict, List

# Real subtype text this project's own checked sample actually
# produced -- NOT an exhaustive list, a starting point from real
# observed descriptions. A real ingestion pass would need to confirm
# full coverage across many more real games before trusting this as
# complete.
HANDLING_ERROR_KEYWORDS = ("Lost Ball Turnover", "Traveling Turnover", "Double Dribble", "Discontinue Dribble")
EXCLUDED_KEYWORDS = ("Bad Pass Turnover", "Offensive Foul Turnover", "Illegal Screen", "Kicked Ball", "Palming")


def classify_turnover_description(description: str) -> str:
    """One real turnover event's description -> a coarse bucket.
    Returns 'handling_error', 'excluded', or 'other_unclassified' (a
    real subtype this small keyword list doesn't yet recognize --
    reported honestly rather than silently mis-bucketed)."""
    if not description:
        return "other_unclassified"
    for kw in HANDLING_ERROR_KEYWORDS:
        if kw in description:
            return "handling_error"
    for kw in EXCLUDED_KEYWORDS:
        if kw in description:
            return "excluded"
    if "Turnover" in description:
        return "other_unclassified"
    return "not_a_turnover"


def fetch_game_turnover_events(game_id: str) -> List[dict]:
    """Every real turnover event in one real game, with the player
    name (parsed from the description's leading word -- a real
    limitation: this is a text-based extraction, not a clean
    PLAYER_ID field, and would need real name-matching work before any
    production use) and its classified bucket."""
    from nba_api.stats.endpoints import playbyplayv3
    df = playbyplayv3.PlayByPlayV3(game_id=game_id, timeout=20).get_data_frames()[0]
    events = []
    for _, row in df.iterrows():
        desc = row.get("description") or ""
        if "Turnover" not in desc:
            continue
        bucket = classify_turnover_description(desc)
        player_name_guess = desc.split(" ")[0] if desc else None
        events.append({"game_id": game_id, "description": desc, "bucket": bucket, "player_last_name_guess": player_name_guess})
    return events


def demo_report(game_ids: List[str]) -> Dict[str, dict]:
    """Small, real, read-only demonstration across a handful of real
    games -- aggregates by the guessed last name (a real limitation,
    see fetch_game_turnover_events) since this prototype has no name-
    to-player_id resolution built yet."""
    totals: Dict[str, dict] = {}
    for gid in game_ids:
        try:
            events = fetch_game_turnover_events(gid)
        except Exception as e:
            print(f"  {gid}: FAILED ({e})")
            continue
        for ev in events:
            key = ev["player_last_name_guess"]
            row = totals.setdefault(key, {"total": 0, "handling_error": 0, "excluded": 0, "other_unclassified": 0})
            row["total"] += 1
            row[ev["bucket"]] = row.get(ev["bucket"], 0) + 1
    return totals


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from loader import load_schedule

    print("=== Ball-security turnover-subtype PROTOTYPE (read-only, small sample) ===\n")
    sample_2023 = [g.game_id for g in load_schedule("2023-24")[:5]]
    sample_1996 = [g.game_id for g in load_schedule("1996-97")[:2]]

    print(f"2023-24 sample ({len(sample_2023)} real games):")
    totals_2023 = demo_report(sample_2023)
    for name, row in sorted(totals_2023.items(), key=lambda kv: -kv[1]["total"])[:10]:
        print(f"  {name:15s} total={row['total']:2d}  handling_error={row.get('handling_error',0):2d}  "
              f"excluded(bad_pass/off_foul/etc)={row.get('excluded',0):2d}  unclassified={row.get('other_unclassified',0):2d}")

    print(f"\n1996-97 sample ({len(sample_1996)} real games) -- era-coverage check:")
    totals_1996 = demo_report(sample_1996)
    for name, row in sorted(totals_1996.items(), key=lambda kv: -kv[1]["total"])[:10]:
        print(f"  {name:15s} total={row['total']:2d}  handling_error={row.get('handling_error',0):2d}  "
              f"excluded(bad_pass/off_foul/etc)={row.get('excluded',0):2d}  unclassified={row.get('other_unclassified',0):2d}")
