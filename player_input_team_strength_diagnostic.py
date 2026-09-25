"""PLAYER INPUTS -> TEAM STRENGTH DIAGNOSTIC V1: features, transparent models and evaluation helpers.

X-ray only. Every feature is read from the pregame player state the detailed engine is given; no
team identity, player identity, net rating, standings or outcome is ever a feature. Models are
ridge regressions (plus one small gradient-boosting probe) validated on expanding chronological
blocks, so no game is ever predicted from its own or a later game.
"""
import json
import math
import statistics as st
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

import player_input_team_strength_dataset as ds
from possession_orchestrator import PlayerSimulationProfile

FIELDS = ds.PLAYER_FIELDS
FIELD_INDEX = {name: i for i, name in enumerate(FIELDS)}
OFF_SIDE_FIELDS = ds.OFFENSE_FIELDS + ("offensive_rebounding_shrunk_rate",) + ds.TENDENCY_FIELDS + ds.ROLE_FIELDS
DEF_SIDE_FIELDS = ds.DEFENSE_FIELDS + ("defensive_rebounding_shrunk_rate",)
ALPHA_GRID = (1.0, 10.0, 100.0, 1000.0, 10000.0)

_SYN = PlayerSimulationProfile.synthetic("1", "HOME")
NEUTRAL = {name: (getattr(_SYN, name) if getattr(_SYN, name) is not None else 0.0) for name in FIELDS}

FAMILIES = {
    "shooting": ("rim_finishing_shrunk_rate", "floater_short_mid_shrunk_rate", "midrange_shrunk_rate",
                 "three_point_shrunk_rate", "free_throw_shrunk_rate"),
    "creation": ("rim_access_creation_shrunk_rate", "foul_drawing_shrunk_rate"),
    "playmaking": ("passing_accuracy_ast_pct", "playmaking_vision_shrunk_rate", "ball_security_error_rate"),
    "tendencies": ds.TENDENCY_FIELDS,
    "roles": ds.ROLE_FIELDS,
    "offensive_rebounding": ("offensive_rebounding_shrunk_rate",),
    "poa_containment": ("poa_containment_shrunk_rate",),
    "rim_protection": ("rim_protection_suppression_rate",),
    "defensive_playmaking": ("defensive_playmaking_per36",),
    "foul_discipline": ("foul_discipline_shrunk_rate",),
    "defensive_rebounding": ("defensive_rebounding_shrunk_rate",),
}
ABILITY_FIELDS = tuple(f for f in FIELDS if f not in ds.TENDENCY_FIELDS and f not in ds.ROLE_FIELDS)
ROLE_TENDENCY_FIELDS = ds.TENDENCY_FIELDS + ds.ROLE_FIELDS


# ---------------------------------------------------------------------
# Team-level summaries from one team's pregame player state
# ---------------------------------------------------------------------
def rotation(team_rows: List[dict]) -> List[dict]:
    """Players expected to play (minutes > 0, not OUT), most minutes first."""
    active = [p for p in team_rows if p["minutes"] > 0 and p["status"] != "OUT"]
    return sorted(active, key=lambda p: -p["minutes"])


def _value(player: dict, name: str) -> float:
    v = player["f"][FIELD_INDEX[name]]
    return NEUTRAL[name] if v is None else float(v)


def _wmean(values, weights):
    total = sum(weights)
    return sum(v * w for v, w in zip(values, weights)) / total if total else 0.0


def field_summaries(rot: List[dict], name: str) -> Dict[str, float]:
    vals = [_value(p, name) for p in rot]
    mins = [p["minutes"] for p in rot]
    top10 = vals[:10]
    top5, top8 = vals[:5], vals[:8]
    rest = vals[5:10]
    six_eight = vals[5:8] or rest
    arr = np.array(top10)
    return {
        "mw": _wmean(vals, mins), "eq": float(np.mean(vals)), "top5": float(np.mean(top5)), "top8": float(np.mean(top8)),
        "bench_gap": float(np.mean(top5) - (np.mean(rest) if rest else np.mean(top5))),
        "s68": float(np.mean(six_eight)) if six_eight else float(np.mean(top5)),
        "min8": float(min(top8)), "max": float(max(vals)), "sd10": float(arr.std()),
        "p25": float(np.percentile(arr, 25)), "p75": float(np.percentile(arr, 75)),
    }


LEVEL_KEYS = ("mw", "eq", "top5", "top8")
DEPTH_KEYS = ("bench_gap", "s68", "min8", "max", "sd10", "p25", "p75")


def availability_features(team_rows: List[dict]) -> Dict[str, float]:
    rot = rotation(team_rows)
    mins = [p["minutes"] for p in rot] or [0.0]
    total = sum(mins)
    return {"total_minutes": total, "n_rotation": float(len(rot)), "top1_minute_share": mins[0] / total if total else 0.0,
            "top5_minute_share": sum(mins[:5]) / total if total else 0.0,
            "minutes_hhi": sum((m / total) ** 2 for m in mins) if total else 0.0,
            "share_status_active": (sum(1 for p in team_rows if p["status"] == "ACTIVE") / len(team_rows)) if team_rows else 0.0}


def interaction_features(rot: List[dict]) -> Dict[str, float]:
    mw = lambda name: _wmean([_value(p, name) for p in rot], [p["minutes"] for p in rot])
    top8 = rot[:8]
    top5 = rot[:5]
    creators = sorted(top5, key=lambda p: -_value(p, "role_off_initiation"))
    others = creators[1:] if creators else []
    non_init_med = st.median([_value(p, "role_off_initiation") for p in rot]) if rot else 0.0
    non_init = [p for p in rot if _value(p, "role_off_initiation") <= non_init_med] or rot
    return {
        "ix__initiation_x_three": mw("role_off_initiation") * mw("three_point_shrunk_rate"),
        "ix__rim_access_x_finishing": mw("rim_access_creation_shrunk_rate") * mw("rim_finishing_shrunk_rate"),
        "ix__ball_security_x_initiation": (-mw("ball_security_error_rate")) * mw("role_off_initiation"),
        "ix__poa_x_rim_protection": mw("poa_containment_shrunk_rate") * mw("rim_protection_suppression_rate"),
        "ix__defplay_x_foul_discipline": mw("defensive_playmaking_per36") * mw("foul_discipline_shrunk_rate"),
        "ix__oreb_x_role_finishing": mw("offensive_rebounding_shrunk_rate") * mw("role_off_finishing"),
        "ix__spacing_around_creator": float(np.mean([_value(p, "three_point_shrunk_rate") for p in others])) if others else 0.0,
        "ix__shooting_among_non_initiators": _wmean([_value(p, "three_point_shrunk_rate") for p in non_init],
                                                    [p["minutes"] for p in non_init]),
        "ix__weak_link_poa": float(min((_value(p, "poa_containment_shrunk_rate") for p in top8), default=0.0)),
        "ix__weak_link_rim_protection": float(min((_value(p, "rim_protection_suppression_rate") for p in top8), default=0.0)),
        "ix__n_credible_shooters": float(sum(1 for p in top8 if _value(p, "three_point_shrunk_rate") >= NEUTRAL["three_point_shrunk_rate"])),
        "ix__n_credible_creators": float(sum(1 for p in top8 if _value(p, "role_off_initiation") >= NEUTRAL["role_off_initiation"])),
        "ix__n_credible_defenders": float(sum(1 for p in top8 if _value(p, "defensive_playmaking_per36") >= NEUTRAL["defensive_playmaking_per36"])),
    }


def team_features(team_rows: List[dict]) -> Dict[str, float]:
    """Every candidate feature for one team, named '<summary>__<field>', 'av__<x>' or 'ix__<x>'."""
    rot = rotation(team_rows)
    out = {}
    for name in FIELDS:
        for key, value in field_summaries(rot, name).items():
            out[f"{key}__{name}"] = value
    for key, value in availability_features(team_rows).items():
        out[f"av__{key}"] = value
    out.update(interaction_features(rot))
    return out


def field_of(feature: str) -> Optional[str]:
    return feature.split("__", 1)[1] if "__" in feature and not feature.startswith(("av__", "ix__")) else None


def select_columns(names: Sequence[str], *, fields: Optional[Sequence[str]] = None, summaries: Optional[Sequence[str]] = None,
                   availability: bool = False, interactions: bool = False, drop_fields: Sequence[str] = (),
                   drop_interactions: bool = False) -> List[str]:
    keep = []
    for n in names:
        if n.startswith("av__"):
            if availability:
                keep.append(n)
        elif n.startswith("ix__"):
            if interactions and not drop_interactions:
                keep.append(n)
        else:
            summary, field = n.split("__", 1)
            if fields is not None and field in fields and (summaries is None or summary in summaries) and field not in drop_fields:
                keep.append(n)
    return keep


# ---------------------------------------------------------------------
# Ridge regression with chronological validation
# ---------------------------------------------------------------------
def fit_ridge(X: np.ndarray, y: np.ndarray, alpha: float):
    mean, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    Z = (X - mean) / sd
    y_mean = y.mean()
    beta = np.linalg.solve(Z.T @ Z + alpha * np.eye(Z.shape[1]), Z.T @ (y - y_mean))
    return mean, sd, beta, y_mean


def predict_ridge(model, X: np.ndarray) -> np.ndarray:
    mean, sd, beta, y_mean = model
    return ((X - mean) / sd) @ beta + y_mean


def pick_alpha(X: np.ndarray, y: np.ndarray, grid=ALPHA_GRID, n_inner: int = 3) -> float:
    """Chronological inner validation inside the TRAINING rows only (rows are date-ordered)."""
    n = len(y)
    cuts = [int(n * (0.4 + 0.2 * k)) for k in range(n_inner)] + [n]
    best, best_err = grid[-1], float("inf")
    for alpha in grid:
        errs = []
        for k in range(n_inner):
            tr, te = slice(0, cuts[k]), slice(cuts[k], cuts[k + 1])
            if cuts[k] < 20 or cuts[k + 1] - cuts[k] < 5:
                continue
            model = fit_ridge(X[tr], y[tr], alpha)
            errs.append(float(np.mean((predict_ridge(model, X[te]) - y[te]) ** 2)))
        if errs and float(np.mean(errs)) < best_err:
            best, best_err = alpha, float(np.mean(errs))
    return best


def metrics(pred: Sequence[float], actual: Sequence[float]) -> dict:
    p, a = np.asarray(pred, float), np.asarray(actual, float)
    resid = p - a
    ss_tot = float(((a - a.mean()) ** 2).sum())
    return {"n": int(len(a)), "pearson": _pearson(p, a), "spearman": _pearson(_ranks(p), _ranks(a)),
            "mae": float(np.abs(resid).mean()), "rmse": float(np.sqrt((resid ** 2).mean())),
            "r2": 1 - float((resid ** 2).sum()) / ss_tot if ss_tot else None}


def _pearson(x, y):
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def _ranks(x):
    x = np.asarray(x, float)
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(len(x))
    for v in np.unique(x):
        idx = x == v
        if idx.sum() > 1:
            ranks[idx] = ranks[idx].mean()
    return ranks


def expanding_blocks(dates: Sequence[str], seasons: Sequence[str], first_test_season: str) -> List[List[int]]:
    """Test blocks = each later season split into two date-ordered halves. Training for a block is
    every row with an EARLIER date than the block's first date (never a later or equal one)."""
    blocks = []
    for season in sorted(set(seasons)):
        if season < first_test_season:
            continue
        idx = sorted((i for i, s in enumerate(seasons) if s == season), key=lambda i: dates[i])
        half = len(idx) // 2
        blocks.extend([idx[:half], idx[half:]])
    return blocks


def cv_predict(X: np.ndarray, y: np.ndarray, dates: Sequence[str], blocks: List[List[int]], alphas=ALPHA_GRID) -> Tuple[np.ndarray, List[dict]]:
    """Out-of-fold predictions for every test row; also per-fold metrics."""
    pred = np.full(len(y), np.nan)
    folds = []
    date_arr = np.asarray(dates)
    for block in blocks:
        start = min(date_arr[i] for i in block)
        train = np.array([i for i in np.argsort(date_arr, kind="stable") if date_arr[i] < start])
        if len(train) < 60:
            continue
        alpha = pick_alpha(X[train], y[train], alphas)
        model = fit_ridge(X[train], y[train], alpha)
        pred[block] = predict_ridge(model, X[block])
        folds.append({"n_train": int(len(train)), "n_test": len(block), "alpha": alpha, **metrics(pred[block], y[block])})
    return pred, folds


# ---------------------------------------------------------------------
# Small gradient-boosting probe (depth-2 trees, squared error) -- ceiling probe only
# ---------------------------------------------------------------------
def _best_split(X, r, idx, min_leaf=15):
    best = (0.0, None, None)
    total = r[idx].sum()
    n = len(idx)
    base = total * total / n
    for j in range(X.shape[1]):
        order = idx[np.argsort(X[idx, j], kind="mergesort")]
        xs, rs = X[order, j], r[order]
        csum = np.cumsum(rs)
        for k in range(min_leaf, n - min_leaf, max(1, n // 24)):
            if xs[k - 1] == xs[k]:
                continue
            gain = csum[k - 1] ** 2 / k + (total - csum[k - 1]) ** 2 / (n - k) - base
            if gain > best[0]:
                best = (gain, j, (xs[k - 1] + xs[k]) / 2)
    return best


def _fit_tree(X, r, depth=2):
    idx = np.arange(len(r))

    def build(ix, d):
        if d == 0 or len(ix) < 40:
            return float(r[ix].mean())
        gain, j, thr = _best_split(X, r, ix)
        if j is None:
            return float(r[ix].mean())
        left, right = ix[X[ix, j] <= thr], ix[X[ix, j] > thr]
        return (j, thr, build(left, d - 1), build(right, d - 1))
    return build(idx, depth)


def _tree_predict(tree, X):
    out = np.empty(len(X))
    for i, row in enumerate(X):
        node = tree
        while isinstance(node, tuple):
            node = node[2] if row[node[0]] <= node[1] else node[3]
        out[i] = node
    return out


def fit_gbm(X, y, rounds=80, lr=0.06):
    base = float(y.mean())
    resid = y - base
    trees = []
    for _ in range(rounds):
        tree = _fit_tree(X, resid)
        resid = resid - lr * _tree_predict(tree, X)
        trees.append(tree)
    return base, lr, trees


def predict_gbm(model, X):
    base, lr, trees = model
    return base + lr * sum(_tree_predict(t, X) for t in trees)


def cv_predict_gbm(X, y, dates, blocks, **kw):
    pred = np.full(len(y), np.nan)
    date_arr = np.asarray(dates)
    for block in blocks:
        start = min(date_arr[i] for i in block)
        train = np.array([i for i in range(len(y)) if date_arr[i] < start])
        if len(train) < 60:
            continue
        pred[block] = predict_gbm(fit_gbm(X[train], y[train], **kw), X[block])
    return pred


# ---------------------------------------------------------------------
# Dataset assembly
# ---------------------------------------------------------------------
def load_records(seasons=ds.SEASONS, base="backtests", from_parts: bool = False) -> List[dict]:
    """Records sorted by (date, game_id). Reads the merged dataset unless the per-season parts are requested."""
    if not from_parts and ds.DATASET_PATH.exists():
        import gzip
        with gzip.open(ds.DATASET_PATH, "rt") as f:
            return sorted(json.load(f)["records"], key=lambda r: (r["date"], r["game_id"]))
    records = []
    for season in seasons:
        with open(f"{base}/_pits_{season}.json") as f:
            records.extend(json.load(f)["records"])
    return sorted(records, key=lambda r: (r["date"], r["game_id"]))


def game_matrix(records: List[dict]):
    """Per-game feature dicts for home and away plus targets."""
    home = [team_features(r["home"]) for r in records]
    away = [team_features(r["away"]) for r in records]
    names = sorted(home[0])
    return names, home, away


def to_array(feature_dicts: List[dict], names: Sequence[str]) -> np.ndarray:
    return np.array([[d[n] for n in names] for d in feature_dicts], float)


def margin_from_od(o_home, o_away, d_home, d_away):
    """Expected home margin = (home offence - away offence) + (away points allowed - home points allowed)."""
    return (o_home - o_away) + (d_away - d_home)


def bucket_of(abs_margin: float) -> str:
    edges = ((2, "near_equal (<2)"), (4, "small (2-4)"), (6, "medium (4-6)"), (9, "large (6-9)"))
    for edge, name in edges:
        if abs_margin < edge:
            return name
    return "extreme (>=9)"


def slope(x: Sequence[float], y: Sequence[float]) -> Optional[dict]:
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 5 or x.std() == 0:
        return None
    b = float(np.cov(x, y, bias=True)[0, 1] / x.var())
    return {"n": int(len(x)), "slope": b, "intercept": float(y.mean() - b * x.mean()), "corr": _pearson(x, y)}


def bootstrap_slope_ci(x, y, n_boot=2000, seed=20260924):
    rng = np.random.default_rng(seed)
    x, y = np.asarray(x, float), np.asarray(y, float)
    vals = []
    for _ in range(n_boot):
        i = rng.integers(0, len(x), len(x))
        if x[i].std() > 0:
            vals.append(np.cov(x[i], y[i], bias=True)[0, 1] / x[i].var())
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]
