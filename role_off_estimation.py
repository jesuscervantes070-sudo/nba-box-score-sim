"""
Phase 13 -- Lineup / Player Role Inference: the estimator.

Builds a `PlayerRoleProfile` for one real, stable NBA `player_id` as of a
given season, from the cached season-level role-off evidence
(role_off_ingestion.load_role_off) plus the defensive deployment axis
(role_off_analysis.defensive_deployment_axis). Strict temporal cutoff:
only the `as_of_season`'s own real evidence is used -- no other-season
data, no future data, ever.
"""
from functools import lru_cache
from typing import Optional

import role_off_analysis as roa
import role_off_ingestion as roi
from role_off_profile import (
    MEASURED_TRACKING, PlayerRoleProfile, RoleObservation, UNAVAILABLE_OBSERVATION,
)


@lru_cache(maxsize=8)
def _defensive_axis_for_season(season: str):
    return roa.defensive_deployment_axis(season)


def build_role_profile(player_id: str, as_of_season: str) -> PlayerRoleProfile:
    if as_of_season < roi.ROLE_TRACKING_FIRST_SEASON:
        return PlayerRoleProfile(player_id=player_id, as_of_season=as_of_season)  # all four default to UNAVAILABLE

    players = roi.load_role_off(as_of_season)
    row = players.get(player_id)

    def _obs(candidate_fn, min_sample_field="MIN"):
        if row is None:
            return UNAVAILABLE_OBSERVATION
        v = candidate_fn(row)
        if v is None:
            return UNAVAILABLE_OBSERVATION
        return RoleObservation(
            value=v, evidence_mode=MEASURED_TRACKING, source="leaguedashptstats/leaguedashplayerstats",
            as_of_season=as_of_season, sample_size=int(row.get(min_sample_field) or 0),
        )

    init_obs = _obs(roa.role_off_initiation)
    finish_obs = _obs(roa.role_off_finishing)
    spacing_obs = _obs(roa.role_off_spacing)

    def_axis = _defensive_axis_for_season(as_of_season)
    def_row = def_axis.get(player_id)
    if def_row is None:
        def_obs = UNAVAILABLE_OBSERVATION
    else:
        def_obs = RoleObservation(
            value=def_row["role_def_perimeter_minus_interior"], evidence_mode=MEASURED_TRACKING,
            source="leagueseasonmatchups+shot_zone_join", as_of_season=as_of_season,
            sample_size=int(def_row["matchup_minutes"]),
            note="matchup-time-weighted opponent shot-zone profile -- deployment context only, not suppression quality",
        )

    return PlayerRoleProfile(
        player_id=player_id, as_of_season=as_of_season,
        role_off_initiation=init_obs, role_off_finishing=finish_obs, role_off_spacing=spacing_obs,
        role_def_perimeter_interior=def_obs,
    )
