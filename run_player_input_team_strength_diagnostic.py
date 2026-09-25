"""Runner: python3 run_player_input_team_strength_diagnostic.py
Reads backtests/_pits_<season>.json (from player_input_team_strength_dataset.py), fits the diagnostic
ladder on expanding chronological blocks, and writes backtests/player_input_team_strength_diagnostic_v1.json."""
import hashlib
import json
import math
import time

import numpy as np

import game_engine as ge
import player_input_team_strength_dataset as ds
import player_input_team_strength_diagnostic as d
from loader import load_teams, load_league_pace_variation

SIGMA = 12.5  # fixed game-margin SD used to turn any margin prediction into a win probability


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def win_metrics(margin_pred, actual_margin):
    p = np.array([norm_cdf(m / SIGMA) for m in margin_pred])
    y = (np.asarray(actual_margin) > 0).astype(float)
    return {"accuracy": float(((p >= 0.5) == (y == 1)).mean()), "brier": float(((p - y) ** 2).mean())}


def aggregate_engine_margin(season, home, away, cache, n=200):
    if season not in cache:
        teams = load_teams(season)
        cache[season] = (teams, ge.compute_league_averages(teams, load_league_pace_variation(season)))
    teams, avg = cache[season]
    if home not in teams or away not in teams:
        return None
    ge._rng = np.random.default_rng(int(hashlib.sha256(f"agg|{season}|{home}|{away}".encode()).hexdigest()[:8], 16))
    margins = []
    for _ in range(n):
        r = ge.simulate_game(teams[home], teams[away], avg)
        margins.append(r.home_score - r.away_score)
    return float(np.mean(margins))


def main():
    t0 = time.time()
    records = d.load_records()
    N = len(records)
    names, home_f, away_f = d.game_matrix(records)
    dates = [r["date"] for r in records]
    seasons = [r["season"] for r in records]
    home_pts = np.array([r["target"]["home_score"] for r in records], float)
    away_pts = np.array([r["target"]["away_score"] for r in records], float)
    actual_margin = home_pts - away_pts

    # row-level (team-game) arrays: first N rows are home teams, next N away teams
    F = d.to_array(home_f + away_f, names)
    is_home = np.r_[np.ones(N), np.zeros(N)][:, None]
    row_dates = dates + dates
    row_seasons = seasons + seasons
    y_off = np.r_[home_pts, away_pts]
    y_def = np.r_[away_pts, home_pts]
    col = {n: i for i, n in enumerate(names)}

    first_test = ds.SEASONS[1]
    blocks_rows = d.expanding_blocks(row_dates, row_seasons, first_test)
    order = sorted(range(2 * N), key=lambda i: (row_dates[i], i))
    # rows must be date-ordered for the inner chronological alpha search
    perm = np.array(order)
    inv = np.empty(2 * N, int)
    inv[perm] = np.arange(2 * N)
    F, is_home = F[perm], is_home[perm]
    y_off, y_def = y_off[perm], y_def[perm]
    row_dates = [row_dates[i] for i in perm]
    row_seasons = [row_seasons[i] for i in perm]
    blocks = d.expanding_blocks(row_dates, row_seasons, first_test)
    game_of_row = np.r_[np.arange(N), np.arange(N)][perm]
    home_row_of_game = {g: r for r, g in enumerate(game_of_row) if perm[r] < N}
    away_row_of_game = {g: r for r, g in enumerate(game_of_row) if perm[r] >= N}
    tested = np.zeros(2 * N, bool)
    for b in blocks:
        tested[b] = True

    all_levels = ("mw", "eq", "top5", "top8")
    OFF_IX = ("ix__initiation_x_three", "ix__rim_access_x_finishing", "ix__ball_security_x_initiation", "ix__oreb_x_role_finishing",
              "ix__spacing_around_creator", "ix__shooting_among_non_initiators", "ix__n_credible_shooters", "ix__n_credible_creators")
    DEF_IX = ("ix__poa_x_rim_protection", "ix__defplay_x_foul_discipline", "ix__weak_link_poa", "ix__weak_link_rim_protection",
              "ix__n_credible_defenders")
    AV = tuple(n for n in names if n.startswith("av__"))
    ALL_IX = tuple(n for n in names if n.startswith("ix__"))

    def X_for(selected):
        return np.hstack([F[:, [col[n] for n in selected]], is_home]) if selected else is_home

    def sel(fields, summaries):
        return [n for n in names if not n.startswith(("av__", "ix__")) and n.split("__", 1)[1] in fields and n.split("__", 1)[0] in summaries]

    A0 = list(AV)
    A1 = sel(d.FIELDS, ("mw",))
    A2 = A1 + sel(d.FIELDS, ("top5", "top8", "eq") + d.DEPTH_KEYS) + A0
    A3 = A2 + list(ALL_IX)
    A4_off = sel(d.OFF_SIDE_FIELDS, ("mw", "top5", "top8", "eq") + d.DEPTH_KEYS) + A0 + list(OFF_IX)
    A4_def = sel(d.DEF_SIDE_FIELDS, ("mw", "top5", "top8", "eq") + d.DEPTH_KEYS) + A0 + list(DEF_IX)
    ladder = {"A0_minutes_availability": (A0, A0), "A1_minute_weighted_means": (A1, A1), "A2_plus_top5_depth_distribution": (A2, A2),
              "A3_plus_interactions": (A3, A3), "A4_offense_defense_separate": (A4_off, A4_def)}

    def run_target(selected, y):
        pred, folds = d.cv_predict(X_for(selected), y, row_dates, blocks)
        m = tested
        return pred, {"overall": d.metrics(pred[m], y[m]), "folds": folds,
                      "fold_r2_mean": float(np.mean([f["r2"] for f in folds])), "fold_r2_sd": float(np.std([f["r2"] for f in folds]))}

    results = {"offense": {}, "defense": {}}
    preds = {}
    for name, (off_cols, def_cols) in ladder.items():
        po, ro = run_target(off_cols, y_off)
        pd_, rd = run_target(def_cols, y_def)
        preds[name] = (po, pd_)
        results["offense"][name] = ro
        results["defense"][name] = rd
        print(name, "off r2", round(ro["overall"]["r2"], 4), "def r2", round(rd["overall"]["r2"], 4), flush=True)

    # ---- game margin: from separate offence/defence models and from a direct home-minus-away model
    game_tested = np.array([tested[home_row_of_game[g]] for g in range(N)])
    margin = {}
    for name, (po, pd_) in preds.items():
        m_hat = np.array([d.margin_from_od(po[home_row_of_game[g]], po[away_row_of_game[g]],
                                           pd_[home_row_of_game[g]], pd_[away_row_of_game[g]]) for g in range(N)])
        margin[name] = m_hat
    # direct: home features minus away features -> home margin (game-level rows, date-ordered)
    gorder = sorted(range(N), key=lambda g: (dates[g], g))
    Fh = F[[home_row_of_game[g] for g in gorder]]
    Fa = F[[away_row_of_game[g] for g in gorder]]
    g_dates = [dates[g] for g in gorder]
    g_seasons = [seasons[g] for g in gorder]
    g_blocks = d.expanding_blocks(g_dates, g_seasons, first_test)
    g_margin = actual_margin[gorder]
    g_index = {g: i for i, g in enumerate(gorder)}
    diff_all = Fh - Fa
    direct_sets = {"direct_A1": [col[n] for n in A1], "direct_A2": [col[n] for n in A2], "direct_A3": [col[n] for n in A3]}
    direct_pred = {}
    for name, ci in direct_sets.items():
        p, folds = d.cv_predict(diff_all[:, ci], g_margin, g_dates, g_blocks)
        direct_pred[name] = np.array([p[g_index[g]] for g in range(N)])
    # nonlinear probe (A3 features)
    gbm_cols = [col[n] for n in A3]
    gp = d.cv_predict_gbm(diff_all[:, gbm_cols], g_margin, g_dates, g_blocks)
    direct_pred["direct_gbm_A3_probe"] = np.array([gp[g_index[g]] for g in range(N)])
    gbm_off = d.cv_predict_gbm(np.hstack([F[:, [col[n] for n in A3]], is_home]), y_off, row_dates, blocks)
    gbm_def = d.cv_predict_gbm(np.hstack([F[:, [col[n] for n in A3]], is_home]), y_def, row_dates, blocks)
    results["offense"]["nonlinear_gbm_A3_probe"] = {"overall": d.metrics(gbm_off[tested], y_off[tested])}
    results["defense"]["nonlinear_gbm_A3_probe"] = {"overall": d.metrics(gbm_def[tested], y_def[tested])}
    margin["OD_gbm_A3_probe"] = np.array([d.margin_from_od(gbm_off[home_row_of_game[g]], gbm_off[away_row_of_game[g]],
                                                          gbm_def[home_row_of_game[g]], gbm_def[away_row_of_game[g]]) for g in range(N)])
    print("gbm off r2", round(results["offense"]["nonlinear_gbm_A3_probe"]["overall"]["r2"], 4), flush=True)

    # net-rating baseline: margin ~ a + b * (nr_home - nr_away), same expanding folds (context-only comparison)
    nr_diff = np.array([r["context_only"]["net_rating_home"] - r["context_only"]["net_rating_away"] for r in records])
    g_nr = nr_diff[gorder][:, None]
    p, _ = d.cv_predict(g_nr, g_margin, g_dates, g_blocks, alphas=(0.001,))
    net_rating_pred = np.array([p[g_index[g]] for g in range(N)])

    def game_metrics(pred, idx):
        idx = np.array(idx)
        out = d.metrics(pred[idx], actual_margin[idx])
        out.update(win_metrics(pred[idx], actual_margin[idx]))
        return out

    tested_games = [g for g in range(N) if game_tested[g]]
    margin_results = {name: game_metrics(pred, tested_games) for name, pred in {**margin, **direct_pred, "net_rating_baseline": net_rating_pred}.items()}

    # ---- same-game comparison against the detailed simulator and the old aggregate engine
    sim_games = [g for g in tested_games if records[g].get("detailed_sim")]
    engine_cache = {}
    agg = {}
    for g in sim_games:
        r = records[g]
        m = aggregate_engine_margin(r["season"], r["context_only"]["home_team"], r["context_only"]["away_team"], engine_cache)
        if m is not None:
            agg[g] = m
    cmp_games = [g for g in sim_games if g in agg]
    sim_margin = np.array([records[g]["detailed_sim"]["margin"] if records[g].get("detailed_sim") else np.nan for g in range(N)])
    agg_margin = np.array([agg.get(g, np.nan) for g in range(N)])
    engine_cmp = {}
    idx = np.array(cmp_games)
    named = {"detailed_engine": sim_margin, "aggregate_engine_old": agg_margin, "net_rating_baseline": net_rating_pred,
             "player_input_direct_A3": direct_pred["direct_A3"], "player_input_direct_A2": direct_pred["direct_A2"],
             "player_input_OD_A4": margin["A4_offense_defense_separate"], "player_input_gbm_probe": direct_pred["direct_gbm_A3_probe"]}
    for name, pred in named.items():
        engine_cmp[name] = {**d.metrics(pred[idx], actual_margin[idx]), **win_metrics(pred[idx], actual_margin[idx])}
    engine_cmp["detailed_engine_own_win_prob"] = {"accuracy": float(np.mean([(records[g]["detailed_sim"]["home_win_prob"] >= 0.5) == (actual_margin[g] > 0) for g in cmp_games])),
                                                  "brier": float(np.mean([(records[g]["detailed_sim"]["home_win_prob"] - (actual_margin[g] > 0)) ** 2 for g in cmp_games]))}
    corr_pairs = {}
    for a in ("detailed_engine", "aggregate_engine_old", "player_input_direct_A3", "net_rating_baseline"):
        for b in ("detailed_engine", "aggregate_engine_old", "player_input_direct_A3", "net_rating_baseline"):
            if a < b:
                corr_pairs[f"{a}__vs__{b}"] = d._pearson(named[a][idx], named[b][idx])

    # ---- transfer slopes on the same games
    diag = direct_pred["direct_A3"][idx]
    transfer = {"detailed_margin_on_diagnostic_margin": d.slope(diag, sim_margin[idx]),
                "actual_margin_on_diagnostic_margin": d.slope(diag, actual_margin[idx]),
                "aggregate_margin_on_diagnostic_margin": d.slope(diag, agg_margin[idx])}
    transfer["detailed_margin_on_diagnostic_margin"]["slope_ci95"] = d.bootstrap_slope_ci(diag, sim_margin[idx])
    transfer["actual_margin_on_diagnostic_margin"]["slope_ci95"] = d.bootstrap_slope_ci(diag, actual_margin[idx])
    po, pd_ = preds["A4_offense_defense_separate"]
    home_rows = np.array([home_row_of_game[g] for g in cmp_games])
    away_rows = np.array([away_row_of_game[g] for g in cmp_games])
    sim_home = np.array([records[g]["detailed_sim"]["home_score"] for g in cmp_games])
    sim_away = np.array([records[g]["detailed_sim"]["away_score"] for g in cmp_games])
    pred_o = np.r_[po[home_rows], po[away_rows]]
    pred_d = np.r_[pd_[home_rows], pd_[away_rows]]
    real_scored = np.r_[home_pts[idx], away_pts[idx]]
    real_allowed = np.r_[away_pts[idx], home_pts[idx]]
    sim_scored = np.r_[sim_home, sim_away]
    sim_allowed = np.r_[sim_away, sim_home]
    transfer["offense"] = {"sim_points_scored_on_predicted_offense": d.slope(pred_o, sim_scored),
                           "real_points_scored_on_predicted_offense": d.slope(pred_o, real_scored)}
    transfer["defense"] = {"sim_points_allowed_on_predicted_defense": d.slope(pred_d, sim_allowed),
                           "real_points_allowed_on_predicted_defense": d.slope(pred_d, real_allowed)}
    spread = {"offense": {"real_points_scored_sd": float(real_scored.std()), "predicted_offense_sd": float(pred_o.std()),
                          "simulated_mean_points_scored_sd": float(sim_scored.std())},
              "defense": {"real_points_allowed_sd": float(real_allowed.std()), "predicted_defense_sd": float(pred_d.std()),
                          "simulated_mean_points_allowed_sd": float(sim_allowed.std())},
              "margin": {"actual_sd": float(actual_margin[idx].std()), "diagnostic_predicted_sd": float(diag.std()),
                         "detailed_engine_sd": float(sim_margin[idx].std()), "aggregate_engine_sd": float(agg_margin[idx].std())}}

    # ---- buckets by |diagnostic margin| (oriented toward the diagnostic favourite)
    buckets = {}
    for g in cmp_games:
        b = d.bucket_of(abs(direct_pred["direct_A3"][g]))
        sign = 1.0 if direct_pred["direct_A3"][g] >= 0 else -1.0
        buckets.setdefault(b, []).append((sign * actual_margin[g], sign * direct_pred["direct_A3"][g], sign * agg_margin[g], sign * sim_margin[g]))
    bucket_table = {b: {"n": len(v), "actual": float(np.mean([x[0] for x in v])), "diagnostic": float(np.mean([x[1] for x in v])),
                        "aggregate_engine": float(np.mean([x[2] for x in v])), "detailed_engine": float(np.mean([x[3] for x in v]))}
                    for b, v in sorted(buckets.items())}

    # ---- top5 / top8 / full rotation and equal vs minute weights (direct margin, single summary each)
    rotation_variants = {}
    for label, summ in (("top5_equal", "top5"), ("top8_equal", "top8"), ("full_rotation_equal", "eq"), ("full_rotation_minute_weighted", "mw")):
        ci = [col[n] for n in sel(d.FIELDS, (summ,))]
        p, _ = d.cv_predict(diff_all[:, ci], g_margin, g_dates, g_blocks)
        pg = np.array([p[g_index[g]] for g in range(N)])
        rotation_variants[label] = game_metrics(pg, tested_games)
    p, _ = d.cv_predict(diff_all[:, [col[n] for n in A0]], g_margin, g_dates, g_blocks)
    rotation_variants["minutes_availability_only"] = game_metrics(np.array([p[g_index[g]] for g in range(N)]), tested_games)

    # ---- ability vs role/tendency (level + depth summaries), offense target and direct margin
    def family_model(fields):
        s = sel(fields, ("mw", "top5", "top8", "eq") + d.DEPTH_KEYS)
        po_, ro = run_target(s + A0, y_off)
        ci = [col[n] for n in s]
        p, _ = d.cv_predict(diff_all[:, ci], g_margin, g_dates, g_blocks)
        return {"offense": ro["overall"], "margin": game_metrics(np.array([p[g_index[g]] for g in range(N)]), tested_games)}
    ability_vs_role = {"ability_only": family_model(d.ABILITY_FIELDS), "role_tendency_only": family_model(d.ROLE_TENDENCY_FIELDS),
                       "ability_plus_role_tendency": family_model(d.FIELDS)}

    # ---- feature-family ablations (offence uses A4 offence set, defence the A4 defence set)
    def ablate(base_cols, y, drop_fields=(), drop_depth=False, drop_ix=False):
        kept = [n for n in base_cols
                if not (n.startswith("ix__") and drop_ix)
                and not (not n.startswith(("av__", "ix__")) and (n.split("__", 1)[1] in drop_fields or (drop_depth and n.split("__", 1)[0] in d.DEPTH_KEYS)))]
        _, r = run_target(kept, y)
        return r
    ablations = {"offense": {}, "defense": {}}
    full_off = results["offense"]["A4_offense_defense_separate"]
    full_def = results["defense"]["A4_offense_defense_separate"]
    for fam in ("shooting", "creation", "playmaking", "tendencies", "roles", "offensive_rebounding"):
        r = ablate(A4_off, y_off, drop_fields=d.FAMILIES[fam])
        ablations["offense"][fam] = {"r2_without": r["overall"]["r2"], "delta_r2": r["overall"]["r2"] - full_off["overall"]["r2"], "delta_mae": r["overall"]["mae"] - full_off["overall"]["mae"]}
    for label, kw in (("depth_and_distribution", {"drop_depth": True}), ("interactions", {"drop_ix": True})):
        r = ablate(A4_off, y_off, **kw)
        ablations["offense"][label] = {"r2_without": r["overall"]["r2"], "delta_r2": r["overall"]["r2"] - full_off["overall"]["r2"], "delta_mae": r["overall"]["mae"] - full_off["overall"]["mae"]}
    for fam in ("poa_containment", "rim_protection", "defensive_playmaking", "foul_discipline", "defensive_rebounding"):
        r = ablate(A4_def, y_def, drop_fields=d.FAMILIES[fam])
        ablations["defense"][fam] = {"r2_without": r["overall"]["r2"], "delta_r2": r["overall"]["r2"] - full_def["overall"]["r2"], "delta_mae": r["overall"]["mae"] - full_def["overall"]["mae"]}
    for label, kw in (("depth_and_distribution", {"drop_depth": True}), ("interactions", {"drop_ix": True})):
        r = ablate(A4_def, y_def, **kw)
        ablations["defense"][label] = {"r2_without": r["overall"]["r2"], "delta_r2": r["overall"]["r2"] - full_def["overall"]["r2"], "delta_mae": r["overall"]["mae"] - full_def["overall"]["mae"]}

    # ---- per-season consistency of the direct model
    per_season = {}
    for season in ds.SEASONS[1:]:
        ids = [g for g in tested_games if seasons[g] == season]
        per_season[season] = {"n_games": len(ids), "direct_A3": game_metrics(direct_pred["direct_A3"], ids),
                              "OD_A4": game_metrics(margin["A4_offense_defense_separate"], ids), "net_rating": game_metrics(net_rating_pred, ids)}


    # ---- per-season engine comparison and incremental-information regressions (same games)
    per_season_engine = {}
    for season in ds.SEASONS[1:]:
        ii = np.array([g for g in cmp_games if seasons[g] == season])
        if len(ii) < 20:
            continue
        per_season_engine[season] = {
            "n": int(len(ii)), "corr_with_actual_margin": {n: d._pearson(named[n][ii], actual_margin[ii]) for n in named},
            "corr_detailed_vs_net_rating": d._pearson(sim_margin[ii], net_rating_pred[ii]),
            "corr_detailed_vs_diagnostic": d._pearson(sim_margin[ii], direct_pred["direct_A3"][ii]),
            "detailed_margin_sd": float(sim_margin[ii].std()), "diagnostic_margin_sd": float(direct_pred["direct_A3"][ii].std())}

    def ols_r2(cols_, y):
        X = np.column_stack([np.ones(len(y))] + cols_)
        b = np.linalg.lstsq(X, y, rcond=None)[0]
        res = y - X @ b
        return float(1 - res @ res / ((y - y.mean()) @ (y - y.mean()))), [float(v) for v in b]
    act, sm, am = actual_margin[idx], sim_margin[idx], agg_margin[idx]
    incremental = {"diagnostic_only": ols_r2([diag], act), "detailed_only": ols_r2([sm], act), "aggregate_only": ols_r2([am], act),
                   "diagnostic_plus_detailed": ols_r2([diag, sm], act), "diagnostic_plus_aggregate": ols_r2([diag, am], act),
                   "detailed_plus_aggregate": ols_r2([sm, am], act), "all_three": ols_r2([diag, sm, am], act)}
    b = np.polyfit(diag, sm, 1)
    incremental["detailed_residual_after_diagnostic"] = {
        "corr_with_actual_margin": d._pearson(sm - np.polyval(b, diag), act), "residual_sd": float((sm - np.polyval(b, diag)).std())}

    # ---- feature-family ablations on the direct margin model (much more signal than per-team points)
    margin_ablations = {}
    full = margin_results["direct_A3"]
    drop_sets = {**{k: v for k, v in d.FAMILIES.items()}, "depth_and_distribution": None, "interactions": None}
    for fam, fields in drop_sets.items():
        kept = []
        for n in A3:
            if n.startswith("ix__"):
                if fam == "interactions":
                    continue
            elif not n.startswith("av__"):
                summ, fld = n.split("__", 1)
                if fam == "depth_and_distribution" and summ in d.DEPTH_KEYS:
                    continue
                if fields is not None and fld in fields:
                    continue
            kept.append(n)
        p_, _ = d.cv_predict(diff_all[:, [col[n] for n in kept]], g_margin, g_dates, g_blocks)
        m = game_metrics(np.array([p_[g_index[g]] for g in range(N)]), tested_games)
        margin_ablations[fam] = {"pearson": m["pearson"], "mae": m["mae"], "delta_pearson": m["pearson"] - full["pearson"], "delta_mae": m["mae"] - full["mae"]}

    # ---- learning curve: does more training data raise the player-input ceiling? (test = last two blocks)
    test_blocks = g_blocks[-2:]
    test_idx = sorted(i for b in test_blocks for i in b)
    start = min(g_dates[i] for i in test_idx)
    prior = [i for i in range(N) if g_dates[i] < start]
    learning = {}
    for n_train in (150, 300, 450, len(prior)):
        tr = np.array(prior[-n_train:])
        Xc = diff_all[:, [col[n] for n in A2]]
        model = d.fit_ridge(Xc[tr], g_margin[tr], d.pick_alpha(Xc[tr], g_margin[tr]))
        pr = d.predict_ridge(model, Xc[test_idx])
        learning[str(len(tr))] = d.metrics(pr, g_margin[test_idx])

    # ---- optional impact-metric probe (prior-season on-court ratings, joined by name; date-safe by construction)
    impact_probe = None
    from pathlib import Path
    names_by_game = {r["game_id"]: r["probe_only_names"] for r in records if r.get("probe_only_names")}
    if len(names_by_game) == N:
        from loader import load_player_advanced_stats
        prev = {"2022-23": "2021-22", "2023-24": "2022-23", "2024-25": "2023-24", "2025-26": "2024-25"}
        adv = {s_: load_player_advanced_stats(prev[s_]) for s_ in ds.SEASONS}
        keys = ("net_rating", "pie", "off_rating", "def_rating")
        league = {s_: {k: float(np.mean([v[k] for v in adv[s_].values()])) for k in keys} for s_ in ds.SEASONS}

        def impact_features(rows, season):
            vals = {k: [] for k in keys}
            w = []
            for name, minutes, status in rows:
                if minutes <= 0 or status == "OUT":
                    continue
                rec = adv[season].get(name)
                shrink = min(1.0, (rec["gp"] * rec["mpg"]) / 1000.0) if rec else 0.0
                for k in keys:
                    base = league[season][k]
                    vals[k].append(base + shrink * ((rec[k] - base) if rec else 0.0))
                w.append(minutes)
            return [float(np.average(vals[k], weights=w)) if w else league[season][k] for k in keys]
        have = [g for g in range(N) if records[g]["game_id"] in names_by_game]
        if len(have) == N:
            imp_h = np.array([impact_features(names_by_game[records[g]["game_id"]]["home"], seasons[g]) for g in range(N)])
            imp_a = np.array([impact_features(names_by_game[records[g]["game_id"]]["away"], seasons[g]) for g in range(N)])
            imp_diff = (imp_h - imp_a)[gorder]
            res = {}
            p_, _ = d.cv_predict(imp_diff, g_margin, g_dates, g_blocks)
            res["impact_only"] = game_metrics(np.array([p_[g_index[g]] for g in range(N)]), tested_games)
            Xa = np.hstack([diff_all[:, [col[n] for n in A2]], imp_diff])
            p_, _ = d.cv_predict(Xa, g_margin, g_dates, g_blocks)
            res["direct_A2_plus_impact"] = game_metrics(np.array([p_[g_index[g]] for g in range(N)]), tested_games)
            res["direct_A2_reference"] = margin_results["direct_A2"]
            impact_probe = res

    out = {
        "feature_version": ds.FEATURE_VERSION, "seasons": ds.SEASONS, "truth_seasons": ds.TRUTH_SEASONS,
        "n_games": N, "n_team_rows": 2 * N, "n_tested_games": len(tested_games), "n_games_with_detailed_sim": len(sim_games),
        "n_games_compared_across_engines": len(cmp_games),
        "methodology": {"folds": "expanding chronological blocks: each later season split into two date-ordered halves; training = all rows dated before the block; ridge alpha chosen by inner chronological validation inside training only",
                        "features": "pregame player state only (minutes, engine profile fields, availability); no team identity, player identity, net rating or outcomes; is_home is the only context column",
                        "holdout": "the frozen 83-game holdout is excluded from the dataset entirely",
                        "win_probability": f"normal CDF of predicted margin with fixed sigma={SIGMA}",
                        "n_features": {k: len(v[0]) for k, v in ladder.items()}},
        "offense": results["offense"], "defense": results["defense"], "margin": margin_results,
        "engine_comparison_same_games": engine_cmp, "engine_pairwise_correlations": corr_pairs,
        "transfer_slopes": transfer, "spread_comparison": spread, "buckets_by_diagnostic_margin": bucket_table,
        "rotation_variants": rotation_variants, "ability_vs_role_tendency": ability_vs_role, "ablations": ablations,
        "per_season": per_season, "per_season_engine_comparison": per_season_engine,
        "incremental_information_regressions": incremental, "margin_ablations_direct_A3": margin_ablations,
        "learning_curve_last_two_blocks": learning, "impact_metric_probe": impact_probe, "runtime_seconds": time.time() - t0,
    }
    with open("backtests/player_input_team_strength_diagnostic_v1.json", "w") as f:
        json.dump(out, f, indent=1, sort_keys=True, default=str)
    print("done", round(time.time() - t0))


if __name__ == "__main__":
    main()
