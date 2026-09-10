"""
Phase 13 -- Lineup / Player Role Inference: candidate definitions,
persistence/contamination diagnostics, and the three natural-experiment
functions (teammate absence, staggered-lineup-context proxy, team switch).

ARCHITECTURAL NOTE (read before touching a formula here): every
candidate below is deliberately defined as a RAW rate/share -- the
un-residualized volume or context signal -- NOT a value with usage/
touches/opportunity already removed. `playmaking_vision` (Phase 8) took
the same underlying POTENTIAL_AST signal and residualized it against
usage/drives/time-of-possession specifically to ISOLATE SKILL. This
phase does the structural opposite on purpose: role_off_initiation IS
that same raw, unresidualized creation-opportunity rate. Ability =
what's left after removing role/opportunity; role = the opportunity
itself. This is the concrete mechanism by which "role != ability" is
enforced here, not just asserted.

No softmax. No forced sum-to-1. Each candidate is scored independently.
"""
from typing import Dict, List, Optional, Tuple

import role_off_ingestion as roi

MIN_MINUTES = 500.0  # real exposure floor for season-level candidates -- below this, touches/passing rates are noisy season-total ratios, not per-36 rates re-estimated per player


def _row(players: Dict[str, dict], pid: str) -> Optional[dict]:
    return players.get(pid)


# --------------------------- candidate definitions (season-level) ---------------------------

def role_off_initiation(row: dict, min_minutes: float = MIN_MINUTES) -> Optional[float]:
    """Raw creation-opportunity rate: POTENTIAL_AST per 36 real minutes.
    Deliberately NOT residualized against usage/touches (see module
    docstring) -- role captures the raw responsibility, not the skill
    left after removing it."""
    m, pa = row.get("MIN"), row.get("POTENTIAL_AST")
    if m is None or pa is None or m < min_minutes:
        return None
    return pa / m * 36.0


def role_off_finishing(row: dict, min_fgm_proxy: float = 30.0) -> Optional[float]:
    """Share of the player's made field goals that were ASSISTED
    (PCT_AST_FGM) -- high = fed shots by others (finisher role), low =
    self-creates most makes (creator role). No raw-FGM count is directly
    available from this cache to gate on, so this uses the season MIN
    floor as its exposure gate instead (same as the other candidates)."""
    m, v = row.get("MIN"), row.get("PCT_AST_FGM")
    if m is None or v is None or m < MIN_MINUTES:
        return None
    return v


def role_off_spacing(row: dict) -> Optional[float]:
    """Share of the player's made 3-pointers that were ASSISTED
    (PCT_AST_3PM) -- a context/role signal (are you fed spot-up 3s, or
    do you create your own?), empirically NOT the same signal as the
    tendency layer's `three_point_preference` (real Pearson r=0.42,
    Spearman rank corr=0.11 on 2023-24 data, n=382 -- see report Sec. 8).
    """
    m, v = row.get("MIN"), row.get("PCT_AST_3PM")
    if m is None or v is None or m < MIN_MINUTES:
        return None
    return v


def role_off_finishing_vs_creating(row: dict) -> Optional[float]:
    """Secondary/diagnostic candidate: DRIVE_AST_PCT (of a player's own
    drives, what share end in an assist to a teammate) -- a second,
    independent creation-responsibility signal, used in the team-switch
    experiment (Sec. 7) alongside role_off_initiation, not part of the
    4 primary candidates."""
    m, v = row.get("MIN"), row.get("DRIVE_AST_PCT")
    if m is None or v is None or m < MIN_MINUTES:
        return None
    return v


CANDIDATES = {
    "role_off_initiation": role_off_initiation,
    "role_off_finishing": role_off_finishing,
    "role_off_spacing": role_off_spacing,
}


# --------------------------- persistence / heldout ---------------------------

def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if sx == 0 or sy == 0:
        return None
    return cov / (sx * sy)


def _spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    def rank(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        for rank_pos, i in enumerate(order):
            r[i] = rank_pos + 1
        return r
    if len(xs) < 3:
        return None
    return _pearson(rank(xs), rank(ys))


def year_to_year_persistence(candidate_name: str, season_a: str, season_b: str) -> dict:
    """Real player-level year-to-year Spearman rank correlation --
    genuine player separation is automatic here (comparing a player to
    THEMSELVES a year later, not leaking across players), consistent
    with the project's no-same-player-leakage requirement in the sense
    that this diagnostic is explicitly ABOUT the same player across
    time, not a train/heldout generalization claim (see
    heldout_prediction below for that)."""
    fn = CANDIDATES[candidate_name]
    a, b = roi.load_role_off(season_a), roi.load_role_off(season_b)
    xs, ys = [], []
    for pid, row_a in a.items():
        row_b = b.get(pid)
        if row_b is None:
            continue
        va, vb = fn(row_a), fn(row_b)
        if va is not None and vb is not None:
            xs.append(va)
            ys.append(vb)
    return {"candidate": candidate_name, "season_a": season_a, "season_b": season_b, "n": len(xs),
            "rank_corr": _spearman(xs, ys), "pearson": _pearson(xs, ys)}


def heldout_prediction(candidate_name: str, train_season: str, heldout_season: str) -> dict:
    """Simplest real baseline: does last season's value predict this
    season's value at all (real player-season heldout, not a random
    split)? Reports raw persistence MAE against a naive "predict last
    year's value" model vs a "predict the league mean" baseline --
    intentionally simple (per phase instruction to test simple
    alternatives before anything complex)."""
    fn = CANDIDATES[candidate_name]
    train, heldout = roi.load_role_off(train_season), roi.load_role_off(heldout_season)
    pairs = []
    train_vals = []
    for pid, row in train.items():
        v = fn(row)
        if v is not None:
            train_vals.append(v)
    league_mean = sum(train_vals) / len(train_vals) if train_vals else None

    for pid, row_t in train.items():
        row_h = heldout.get(pid)
        if row_h is None:
            continue
        vt, vh = fn(row_t), fn(row_h)
        if vt is not None and vh is not None:
            pairs.append((vt, vh))

    if not pairs or league_mean is None:
        return {"candidate": candidate_name, "insufficient_data": True}

    last_year_mae = sum(abs(vt - vh) for vt, vh in pairs) / len(pairs)
    mean_baseline_mae = sum(abs(league_mean - vh) for _, vh in pairs) / len(pairs)
    return {"candidate": candidate_name, "n": len(pairs), "league_mean_baseline_mae": mean_baseline_mae,
            "last_year_value_mae": last_year_mae, "improvement_over_mean_baseline": mean_baseline_mae - last_year_mae}


# --------------------------- natural experiments ---------------------------

def teammate_absence_experiment(season: str, team_name: str, high_usage_player: str,
                                 subject_player: str, min_absence_games: int = 15) -> dict:
    """Compares `subject_player`'s real deployment evidence in the
    window `high_usage_player` was on the floor for the team vs a real,
    contiguous stretch they missed -- NOT treated as perfectly exogenous
    (see report Sec. 5): a coach's own adjustments during a real injury
    absence are part of what's being measured, not a controlled removal
    of one input holding everything else fixed."""
    window = roi.find_significant_absence_window(season, team_name, high_usage_player, min_absence_games)
    if window is None:
        return {"error": f"no real contiguous absence of >= {min_absence_games} games found for {high_usage_player} in {season}"}
    absent_start, absent_end, n_games = window

    df = roi._fetch_normalized_game_log(season)
    team_dates = df[df["TEAM_NAME"] == team_name].drop_duplicates("GAME_ID").sort_values("GAME_DATE")
    present_start = str(team_dates["GAME_DATE"].min())[:10]
    before = team_dates[team_dates["GAME_DATE"] < absent_start]["GAME_DATE"]
    present_end = str(before.max())[:10] if len(before) else absent_start

    def _fmt(d):
        y, m, day = d.split("-")
        return f"{m}/{day}/{y}"

    poss_present = roi.fetch_pt_stats(season, "Possessions", _fmt(present_start), _fmt(present_end))
    poss_absent = roi.fetch_pt_stats(season, "Possessions", _fmt(absent_start), _fmt(absent_end))
    drives_present = roi.fetch_pt_stats(season, "Drives", _fmt(present_start), _fmt(present_end))
    drives_absent = roi.fetch_pt_stats(season, "Drives", _fmt(absent_start), _fmt(absent_end))
    passing_present = roi.fetch_pt_stats(season, "Passing", _fmt(present_start), _fmt(present_end))
    passing_absent = roi.fetch_pt_stats(season, "Passing", _fmt(absent_start), _fmt(absent_end))
    scoring_present = roi.fetch_scoring_stats(season, _fmt(present_start), _fmt(present_end))
    scoring_absent = roi.fetch_scoring_stats(season, _fmt(absent_start), _fmt(absent_end))

    def _get(df, name, cols):
        rows = df[df["PLAYER_NAME"] == name]
        if rows.empty:
            return None
        r = rows.iloc[0]
        return {c: r.get(c) for c in cols}

    present = {}
    absent = {}
    for label, dfp, dfa, cols in [
        ("possessions", poss_present, poss_absent, ["MIN", "TOUCHES", "TIME_OF_POSS"]),
        ("drives", drives_present, drives_absent, ["DRIVES", "DRIVE_AST", "DRIVE_AST_PCT"]),
        ("passing", passing_present, passing_absent, ["POTENTIAL_AST", "PASSES_MADE"]),
        ("scoring", scoring_present, scoring_absent, ["PCT_AST_FGM", "PCT_UAST_FGM", "PCT_AST_3PM"]),
    ]:
        present[label] = _get(dfp, subject_player, cols)
        absent[label] = _get(dfa, subject_player, cols)

    return {
        "season": season, "team": team_name, "high_usage_player": high_usage_player, "subject_player": subject_player,
        "present_window": (present_start, present_end), "absent_window": (absent_start, absent_end, n_games),
        "present": present, "absent": absent,
    }


def team_switch_experiment(season: str, player_name: str, team_a: str, team_b: str,
                            trade_date: str, onboarding_buffer_days: int = 2) -> dict:
    """Compares `player_name`'s real deployment evidence on `team_a`
    (before the real trade date) vs `team_b` (after it, within the same
    real season) -- a portability/system diagnostic, NOT proof of
    constant production ability (a genuinely different team context is
    exactly the confound being measured, not controlled away)."""
    from datetime import datetime, timedelta
    trade_dt = datetime.strptime(trade_date, "%Y-%m-%d")
    df = roi._fetch_normalized_game_log(season)
    season_start = str(df["GAME_DATE"].min())[:10] if False else None  # not used -- keep team-specific ranges below instead

    team_a_dates = df[df["TEAM_NAME"] == team_a].drop_duplicates("GAME_ID")["GAME_DATE"]
    team_b_dates = df[df["TEAM_NAME"] == team_b].drop_duplicates("GAME_ID")["GAME_DATE"]
    pre_start = str(team_a_dates.min())[:10]
    pre_end = str((trade_dt - timedelta(days=1)).date())
    post_start = str((trade_dt + timedelta(days=onboarding_buffer_days)).date())
    post_end = str(team_b_dates.max())[:10]

    def _fmt(d):
        y, m, day = d.split("-")
        return f"{m}/{day}/{y}"

    drives_a = roi.fetch_pt_stats(season, "Drives", _fmt(pre_start), _fmt(pre_end))
    drives_b = roi.fetch_pt_stats(season, "Drives", _fmt(post_start), _fmt(post_end))
    passing_a = roi.fetch_pt_stats(season, "Passing", _fmt(pre_start), _fmt(pre_end))
    passing_b = roi.fetch_pt_stats(season, "Passing", _fmt(post_start), _fmt(post_end))
    scoring_a = roi.fetch_scoring_stats(season, _fmt(pre_start), _fmt(pre_end))
    scoring_b = roi.fetch_scoring_stats(season, _fmt(post_start), _fmt(post_end))
    poss_a = roi.fetch_pt_stats(season, "Possessions", _fmt(pre_start), _fmt(pre_end))
    poss_b = roi.fetch_pt_stats(season, "Possessions", _fmt(post_start), _fmt(post_end))

    def _get(df, name, cols):
        rows = df[df["PLAYER_NAME"] == name]
        if rows.empty:
            return None
        r = rows.iloc[0]
        return {c: r.get(c) for c in cols}

    team_a_result = {
        "possessions": _get(poss_a, player_name, ["MIN", "TOUCHES", "TIME_OF_POSS"]),
        "drives": _get(drives_a, player_name, ["DRIVES", "DRIVE_AST", "DRIVE_AST_PCT"]),
        "passing": _get(passing_a, player_name, ["POTENTIAL_AST", "PASSES_MADE"]),
        "scoring": _get(scoring_a, player_name, ["PCT_AST_FGM", "PCT_UAST_FGM", "PCT_AST_3PM"]),
    }
    team_b_result = {
        "possessions": _get(poss_b, player_name, ["MIN", "TOUCHES", "TIME_OF_POSS"]),
        "drives": _get(drives_b, player_name, ["DRIVES", "DRIVE_AST", "DRIVE_AST_PCT"]),
        "passing": _get(passing_b, player_name, ["POTENTIAL_AST", "PASSES_MADE"]),
        "scoring": _get(scoring_b, player_name, ["PCT_AST_FGM", "PCT_UAST_FGM", "PCT_AST_3PM"]),
    }
    return {"season": season, "player": player_name, "team_a": team_a, "team_b": team_b,
            "team_a_window": (pre_start, pre_end), "team_b_window": (post_start, post_end),
            "team_a": team_a_result, "team_b": team_b_result}


# --------------------------- defensive deployment (coarse role) ---------------------------

def _parse_matchup_minutes(s: str) -> float:
    m, sec = s.split(":")
    return int(m) + int(sec) / 60.0


def defensive_deployment_axis(season: str, min_matchup_minutes: float = 200.0) -> Dict[str, dict]:
    """Real, matchup-time-weighted average of each defender's opponents'
    OWN shot-zone profile (rim share vs 3PT share, from the real, already
    -cached Phase 5 shot-zone data) -- a coarse "who does this player
    guard" signal, NOT a measure of how well they guard them (that's
    poa_containment's job, untouched here) and NOT physical size or team
    scheme by construction (though both are expected to correlate with
    it -- see report Sec. 12)."""
    import poa_containment_ingestion as poa
    import shot_zone_ingestion as sz

    df = poa.fetch_matchup_data(season)
    zones = sz.load_shot_zones(season)

    opp_profile = {}
    for pid, z in zones.items():
        total = (z["restricted_area_fga"] + z["paint_non_ra_fga"] + z["midrange_fga"]
                 + z["above_break3_fga"] + z["corner3_combined_fga"] + z["backcourt_fga"])
        if total < 50:
            continue
        opp_profile[pid] = {
            "rim_share": z["restricted_area_fga"] / total,
            "three_share": (z["above_break3_fga"] + z["corner3_combined_fga"]) / total,
        }

    by_def: Dict[str, dict] = {}
    for _, row in df.iterrows():
        off_id = str(int(row["OFF_PLAYER_ID"]))
        def_id = str(int(row["DEF_PLAYER_ID"]))
        if off_id not in opp_profile:
            continue
        mt = _parse_matchup_minutes(row["MATCHUP_MIN"])
        if mt <= 0:
            continue
        d = by_def.setdefault(def_id, {"player_name": row["DEF_PLAYER_NAME"], "wsum_rim": 0.0, "wsum_three": 0.0, "wsum_min": 0.0})
        d["wsum_rim"] += opp_profile[off_id]["rim_share"] * mt
        d["wsum_three"] += opp_profile[off_id]["three_share"] * mt
        d["wsum_min"] += mt

    results = {}
    for def_id, d in by_def.items():
        if d["wsum_min"] < min_matchup_minutes:
            continue
        opp_rim = d["wsum_rim"] / d["wsum_min"]
        opp_three = d["wsum_three"] / d["wsum_min"]
        results[def_id] = {
            "player_name": d["player_name"], "matchup_minutes": d["wsum_min"],
            "opponent_rim_weighted_share": opp_rim, "opponent_three_weighted_share": opp_three,
            "role_def_perimeter_minus_interior": opp_three - opp_rim,
        }
    return results
