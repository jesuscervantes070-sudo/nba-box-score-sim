"""OFFENSIVE TRANSLATION FAILURE DIAGNOSTIC V1: analysis helpers (pure functions over the cached simulations,
the pregame player-state dataset and real box-score components). Measurement only."""
import math
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

import historical_game_outcome as hgo
import player_input_team_strength_diagnostic as d
from player_team_stints import team_as_of_date

SIDES = ("home", "away")


# ---------------------------------------------------------------- real box-score components
def real_components(record: dict, index_cache: Dict[str, dict]) -> Optional[Dict[str, dict]]:
    """Real per-team shooting/points components for one game from the cached per-player game log."""
    season = record["season"]
    if season not in index_cache:
        index_cache[season] = hgo._game_log_index(season)
    rows = index_cache[season].get(record["game_id"], ())
    if not rows:
        return None
    home, away = record["context_only"]["home_team"], record["context_only"]["away_team"]
    totals = {home: dict(fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0), away: dict(fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0)}
    for pid, row in rows:
        team = team_as_of_date(pid, record["date"], season)
        if team in totals:
            for k in totals[team]:
                totals[team][k] += row.get(k, 0) or 0
    out = {}
    for side, team in (("home", home), ("away", away)):
        t = totals[team]
        if t["fga"] == 0:
            return None
        out[side] = {"points": 2 * t["fgm"] + t["fg3m"] + t["ftm"], "efg": (t["fgm"] + 0.5 * t["fg3m"]) / t["fga"],
                     "three_rate": t["fg3a"] / t["fga"], "ft_rate": t["fta"] / t["fga"],
                     "twop": (t["fgm"] - t["fg3m"]) / max(1, t["fga"] - t["fg3a"]),
                     "threep": t["fg3m"] / t["fg3a"] if t["fg3a"] else float("nan"), "ftp": t["ftm"] / t["fta"] if t["fta"] else float("nan")}
    return out


# ---------------------------------------------------------------- simulated team-game summaries from the cache
def sim_summary(game: dict, side: str) -> dict:
    a = game["arrays"][side.upper()]
    n = max(1, len(a["points"]))
    m = {k: float(np.mean(v)) for k, v in a.items()}
    poss, fga = m["poss"], max(1e-9, m["fga"])
    fam_n = defaultdict(float)
    exp, real, att = defaultdict(float), defaultdict(float), defaultdict(float)
    for key, v in game["families"].items():
        s, label = key.split("|", 1)
        if s == side.upper():
            att[label] = v[0] / n
            exp[label] = v[2] / n
            real[label] = v[3] / n
    return {"points": m["points"], "poss": poss, "ortg": m["points"] / poss * 100, "efg": (m["fgm"] + 0.5 * m["fg3m"]) / fga,
            "three_rate": m["fg3a"] / fga, "ft_rate": m["fta"] / fga, "twop": (m["fgm"] - m["fg3m"]) / max(1e-9, fga - m["fg3a"]),
            "threep": m["fg3m"] / max(1e-9, m["fg3a"]), "ftp": m["ftm"] / max(1e-9, m["fta"]), "tov100": m["tov"] / poss * 100,
            "orb100": m["oreb"] / poss * 100, "fga100": fga / poss * 100, "exp_fg_pts_unblocked": m["exp_fg_pts_unblocked"],
            "exp_fg_pts": m["exp_fg_pts"], "real_fg_pts": m["real_fg_pts"], "open_fga": m["open_fga"], "blocked": m["blocked"],
            "fouled_shots": m["fouled_shots"], "ftm": m["ftm"], "fta": m["fta"], "tov": m["tov"], "oreb": m["oreb"],
            "attempts_by_label": dict(att), "exp_pts_by_label": dict(exp), "real_pts_by_label": dict(real),
            "points_sd_across_sims": float(np.std(a["points"]))}


# ---------------------------------------------------------------- statistics
def pearson(x, y) -> Optional[float]:
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = ~(np.isnan(x) | np.isnan(y))
    x, y = x[ok], y[ok]
    if len(x) < 3 or x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def spearman(x, y) -> Optional[float]:
    return pearson(d._ranks(np.asarray(x, float)), d._ranks(np.asarray(y, float)))


def ols(X: np.ndarray, y: np.ndarray) -> dict:
    """OLS with intercept; returns coefficients, standard errors and R^2."""
    n = len(y)
    A = np.column_stack([np.ones(n), X])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ beta
    dof = max(1, n - A.shape[1])
    sigma2 = float(resid @ resid / dof)
    cov = sigma2 * np.linalg.pinv(A.T @ A)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {"coef": beta[1:].tolist(), "se": np.sqrt(np.diag(cov))[1:].tolist(), "r2": 1 - float(resid @ resid) / ss_tot if ss_tot else None,
            "resid_sd": float(np.sqrt(sigma2))}


def zscore(v) -> np.ndarray:
    v = np.asarray(v, float)
    sd = v.std()
    return (v - v.mean()) / sd if sd else v * 0


def classify_response(real_coef: float, real_se: float, sim_coef: float) -> str:
    """Flags used in the real-vs-sim table (per-SD effects in points per game)."""
    t = abs(real_coef) / real_se if real_se else 0.0
    if t >= 1.5 and real_coef * sim_coef < 0:
        return "WRONG SIGN"
    if t >= 2 and abs(sim_coef) < 0.2 * abs(real_coef):
        return "NEAR-ZERO SIM"
    if abs(sim_coef) > 3 * max(abs(real_coef), real_se) and abs(sim_coef) > 0.5:
        return "EXAGGERATED SIM"
    return "ok"


# ---------------------------------------------------------------- variance decomposition (four factors + pace)
def four_factor_decomposition(rows: Dict[str, np.ndarray]) -> dict:
    """rows: per-simulated-team-game arrays points, poss, fga, fgm, fg3m, fta, tov, oreb. Decomposes Var(ORtg) with a
    standardized-regression attribution: share_k = cov(ORtg, beta_k x_k) / Var(ORtg)."""
    poss = rows["poss"]
    ortg = rows["points"] / poss * 100
    feats = {"shot_conversion_efg": (rows["fgm"] + 0.5 * rows["fg3m"]) / np.maximum(rows["fga"], 1),
             "turnovers_per_100": rows["tov"] / poss * 100, "offensive_rebounds_per_100": rows["oreb"] / poss * 100,
             "free_throw_rate": rows["fta"] / np.maximum(rows["fga"], 1), "shot_volume_fga_per_100": rows["fga"] / poss * 100}
    X = np.column_stack(list(feats.values()))
    fit = ols(X, ortg)
    var = float(ortg.var())
    shares = {k: float(np.cov(ortg, b * x, bias=True)[0, 1] / var) for (k, x), b in zip(feats.items(), fit["coef"])}
    log_pts, log_poss = np.log(np.maximum(rows["points"], 1)), np.log(poss)
    return {"n": int(len(ortg)), "ortg_sd": float(np.sqrt(var)), "r2_four_factors_plus_volume": fit["r2"], "shares_of_ortg_variance": shares,
            "residual_share": 1 - float(sum(shares.values())),
            "points_variance_from_pace": float(np.cov(log_pts, log_poss, bias=True)[0, 1] / log_pts.var())}


# ---------------------------------------------------------------- Monte Carlo convergence
def mc_convergence(raw: Dict[str, dict], ns=(20, 40, 100, 250, 500)) -> dict:
    out = {"per_n": {}, "games": len(raw)}
    ref_margin, ref_pts, ref_win = {}, {}, {}
    for gid, v in raw.items():
        m = np.array(v["home"]) - np.array(v["away"])
        ref_margin[gid], ref_pts[gid], ref_win[gid] = float(m.mean()), float(np.mean(v["home"])), float((m > 0).mean())
    sd_margin = float(np.mean([np.std(np.array(v["home"]) - np.array(v["away"])) for v in raw.values()]))
    for n in ns:
        dev_margin, dev_pts, dev_win = [], [], []
        for gid, v in raw.items():
            if len(v["home"]) < n:
                continue
            m = (np.array(v["home"])[:n] - np.array(v["away"])[:n])
            dev_margin.append(float(m.mean() - ref_margin[gid]))
            dev_pts.append(float(np.mean(v["home"][:n]) - ref_pts[gid]))
            dev_win.append(float((m > 0).mean() - ref_win[gid]))
        out["per_n"][str(n)] = {
            "theoretical_se_margin": sd_margin / math.sqrt(n), "rms_deviation_margin_vs_n500": float(np.sqrt(np.mean(np.square(dev_margin)))),
            "rms_deviation_home_points_vs_n500": float(np.sqrt(np.mean(np.square(dev_pts)))),
            "rms_deviation_home_win_prob_vs_n500": float(np.sqrt(np.mean(np.square(dev_win))))}
    out["mean_margin_sd_within_game"] = sd_margin
    return out


def noise_share_of_residual(sd_margin: float, n: int, residual_sd: float) -> float:
    return (sd_margin ** 2 / n) / residual_sd ** 2


# ---------------------------------------------------------------- real full-season team box totals (report-only targets)
def real_season_box(seasons: Sequence[str]) -> Dict[Tuple[str, str], dict]:
    """Full-season real team totals from the cached per-player game logs (report-only: in-sample for the season)."""
    from game_metadata import get_game_metadata
    out = {}
    for season in seasons:
        tot = defaultdict(lambda: defaultdict(float))
        games = defaultdict(int)
        for gid, rows in hgo._game_log_index(season).items():
            meta = get_game_metadata(gid, season)
            if meta is None:
                continue
            seen = set()
            for pid, row in rows:
                team = team_as_of_date(pid, meta.game_date, season)
                if team in (meta.home_team, meta.away_team):
                    seen.add(team)
                    for k in ("fgm", "fga", "fg3m", "fg3a", "ftm", "fta"):
                        tot[team][k] += row.get(k, 0) or 0
            for team in seen:
                games[team] += 1
        for team, t in tot.items():
            g = games[team]
            att = t["fga"] + 0.44 * t["fta"]
            pts = 2 * t["fgm"] + t["fg3m"] + t["ftm"]
            out[(season, team)] = {"games": g, "points": pts / g, "attempts": att / g, "fga": t["fga"] / g, "fta": t["fta"] / g,
                                   "ts": pts / (2 * att), "three_rate": t["fg3a"] / t["fga"], "ft_rate": t["fta"] / t["fga"], "ftp": t["ftm"] / t["fta"]}
    return out


def team_season_efficiency_vs_volume(rows_by_team: Dict[Tuple[str, str], dict], real: Dict[Tuple[str, str], dict], comps: Dict[str, Dict[Tuple[str, str], float]]) -> dict:
    """rows_by_team[(season, team)] = simulated mean per-game components averaged over the team's sampled games."""
    keys = [k for k in rows_by_team if k in real]
    def col(f_real, f_sim):
        a = np.array([f_real(real[k]) for k in keys]); b = np.array([f_sim(rows_by_team[k]) for k in keys])
        return {"real_sd": float(a.std()), "sim_sd": float(b.std()), "corr": pearson(a, b), "real_mean": float(a.mean()), "sim_mean": float(b.mean())}
    table = {"points_per_game": col(lambda r: r["points"], lambda s: s["points"]),
             "efficiency_points_per_shooting_attempt": col(lambda r: r["ts"] * 2, lambda s: s["points"] / (s["fga"] + 0.44 * s["fta"])),
             "shooting_attempt_volume_per_game": col(lambda r: r["attempts"], lambda s: s["fga"] + 0.44 * s["fta"]),
             "fga_per_game": col(lambda r: r["fga"], lambda s: s["fga"]), "fta_per_game": col(lambda r: r["fta"], lambda s: s["fta"]),
             "three_point_attempt_rate": col(lambda r: r["three_rate"], lambda s: s["fg3a"] / s["fga"]),
             "free_throw_rate": col(lambda r: r["ft_rate"], lambda s: s["fta"] / s["fga"]), "free_throw_pct": col(lambda r: r["ftp"], lambda s: s["ftm"] / s["fta"])}
    r_pts = np.array([real[k]["points"] for k in keys]); s_pts = np.array([rows_by_team[k]["points"] for k in keys])
    r_ts = np.array([real[k]["ts"] for k in keys]); s_ts = np.array([rows_by_team[k]["points"] / (2 * (rows_by_team[k]["fga"] + 0.44 * rows_by_team[k]["fta"])) for k in keys])
    r_att = np.array([real[k]["attempts"] for k in keys]); s_att = np.array([rows_by_team[k]["fga"] + 0.44 * rows_by_team[k]["fta"] for k in keys])
    s_oreb = np.array([rows_by_team[k]["oreb"] for k in keys]); s_poss = np.array([rows_by_team[k]["poss"] for k in keys]); s_tov = np.array([rows_by_team[k]["tov"] for k in keys])
    fam = {}
    for f, cmap in comps.items():
        x = np.array([cmap[k] for k in keys])
        fam[f] = {"efficiency_corr_real": pearson(x, r_ts), "efficiency_corr_sim": pearson(x, s_ts),
                  "volume_corr_real": pearson(x, r_att), "volume_corr_sim": pearson(x, s_att),
                  "points_corr_real": pearson(x, r_pts), "points_corr_sim": pearson(x, s_pts),
                  "sim_oreb_corr": pearson(x, s_oreb), "sim_possessions_corr": pearson(x, s_poss), "sim_turnovers_corr": pearson(x, s_tov)}
    log_share = {"sim_efficiency_share_of_log_points_variance": float(np.cov(np.log(s_pts), np.log(s_ts), bias=True)[0, 1] / np.var(np.log(s_pts))),
                 "sim_volume_share_of_log_points_variance": float(np.cov(np.log(s_pts), np.log(s_att), bias=True)[0, 1] / np.var(np.log(s_pts))),
                 "real_efficiency_share_of_log_points_variance": float(np.cov(np.log(r_pts), np.log(r_ts), bias=True)[0, 1] / np.var(np.log(r_pts))),
                 "real_volume_share_of_log_points_variance": float(np.cov(np.log(r_pts), np.log(r_att), bias=True)[0, 1] / np.var(np.log(r_pts)))}
    return {"n_team_seasons": len(keys), "components": table, "family_composites": fam, "points_variance_shares": log_share,
            "sim_volume_drivers": {"corr_attempts_with_oreb": pearson(s_att, s_oreb), "corr_attempts_with_possessions": pearson(s_att, s_poss),
                                   "corr_attempts_with_turnovers": pearson(s_att, s_tov), "oreb_sd": float(s_oreb.std()), "possessions_sd": float(s_poss.std())}}
