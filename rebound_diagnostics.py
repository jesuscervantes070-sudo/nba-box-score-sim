"""Read-only rebound-pipeline reconciliation and causal diagnostics."""
import math
from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, Sequence, Tuple

from detailed_engine_benchmark import BenchmarkGame
from detailed_game import DetailedGameResult
from possession_events import EventType
from rebound_resolution import BoxOutState, ReboundOutcome, ReboundSource


@dataclass(frozen=True)
class ReboundFamilyStats:
    opportunities: int
    offensive_rebounds: int
    defensive_rebounds: int
    team_offensive_rebounds: int
    team_defensive_rebounds: int
    mean_eligible_offense: float
    mean_eligible_defense: float

    @property
    def oreb_pct(self) -> float:
        total = self.offensive_rebounds + self.defensive_rebounds
        return self.offensive_rebounds / total if total else 0.0


@dataclass(frozen=True)
class ReboundDiagnosis:
    games: int
    possessions: int
    field_goal_misses: int
    final_missed_free_throw_opportunities: int
    reboundable_misses: int
    rebound_opportunities: int
    duplicate_opportunities: int
    misses_without_opportunity: int
    opportunities_without_miss: int
    offensive_rebounds: int
    defensive_rebounds: int
    direct_offensive_rebounds: int
    direct_defensive_rebounds: int
    team_offensive_rebounds: int
    team_defensive_rebounds: int
    unresolved_rebound_outcomes: int
    rebound_opportunities_beginning_loose: int
    by_family: Dict[str, ReboundFamilyStats]
    by_zone: Dict[str, int]
    source_counts: Dict[str, int]
    mean_eligible_offense: float
    mean_eligible_defense: float
    offense_eligible_opportunities: int
    defense_eligible_opportunities: int
    contested_opportunities: int
    offense_contested_wins: int
    defense_contested_wins: int
    expected_offense_contested_win_rate: float
    zero_eligible_opportunities: int
    all_five_offense_eligible: int
    all_five_defense_eligible: int
    offense_leverage_opportunities: int
    defense_leverage_opportunities: int
    neutral_leverage_opportunities: int
    advantage_present_but_unused: int
    possessions_with_one_oreb: int
    possessions_with_two_oreb: int
    possessions_with_three_plus_oreb: int
    possessions_with_repeat_oreb: int
    repeat_orebs_beyond_first: int
    maximum_oreb_chain: int
    fga_after_oreb: int
    total_fga: int
    event_offensive_rebounds: int
    event_defensive_rebounds: int
    accounting_mismatches: Tuple[str, ...]

    @property
    def opportunities_per_reboundable_miss(self) -> float:
        return self.rebound_opportunities / self.reboundable_misses if self.reboundable_misses else 0.0

    @property
    def oreb_pct(self) -> float:
        total = self.offensive_rebounds + self.defensive_rebounds
        return self.offensive_rebounds / total if total else 0.0

    @property
    def dreb_pct(self) -> float:
        return 1.0 - self.oreb_pct if self.offensive_rebounds + self.defensive_rebounds else 0.0

    @property
    def offense_contested_win_rate(self) -> float:
        return self.offense_contested_wins / self.contested_opportunities if self.contested_opportunities else 0.0

    @property
    def defense_contested_win_rate(self) -> float:
        return self.defense_contested_wins / self.contested_opportunities if self.contested_opportunities else 0.0

    @property
    def fga_after_oreb_share(self) -> float:
        return self.fga_after_oreb / self.total_fga if self.total_fga else 0.0


def _results(items: Sequence[object]) -> Iterable[DetailedGameResult]:
    for item in items:
        yield item.result if isinstance(item, BenchmarkGame) else item


def _family_stats(rows: Sequence[dict]) -> ReboundFamilyStats:
    offensive = sum(row["outcome"] in {
        ReboundOutcome.SECURED_OFFENSE, ReboundOutcome.TEAM_REBOUND_OFFENSE,
    } for row in rows)
    defensive = sum(row["outcome"] in {
        ReboundOutcome.SECURED_DEFENSE, ReboundOutcome.TEAM_REBOUND_DEFENSE,
    } for row in rows)
    return ReboundFamilyStats(
        opportunities=len(rows),
        offensive_rebounds=offensive,
        defensive_rebounds=defensive,
        team_offensive_rebounds=sum(row["outcome"] == ReboundOutcome.TEAM_REBOUND_OFFENSE for row in rows),
        team_defensive_rebounds=sum(row["outcome"] == ReboundOutcome.TEAM_REBOUND_DEFENSE for row in rows),
        mean_eligible_offense=(sum(row["eligible_offensive_count"] for row in rows) / len(rows) if rows else 0.0),
        mean_eligible_defense=(sum(row["eligible_defensive_count"] for row in rows) / len(rows) if rows else 0.0),
    )


def _expected_offense_probability(row: dict) -> float:
    eligible = [candidate for candidate in row["candidates"] if candidate["eligible"]]
    if not eligible:
        return 0.0
    weights = [math.exp(candidate["acquisition_log_weight"]) for candidate in eligible]
    total = sum(weights)
    return sum(weight for candidate, weight in zip(eligible, weights)
               if candidate["side"] == "OFFENSE") / total


def diagnose_rebounds(items: Sequence[object]) -> ReboundDiagnosis:
    results = tuple(_results(items))
    records = [record for result in results for record in result.possessions]
    rows = [row for record in records for row in record.terminal_result.world.rebound_opportunity_log]

    field_goal_misses = sum(
        not shot.get("made")
        for record in records for shot in record.terminal_result.world.shot_attempt_log
    )
    final_ft = sum(row["source"] == ReboundSource.FINAL_MISSED_FT for row in rows)
    reboundable_misses = field_goal_misses + final_ft
    per_record_mismatches = []
    duplicate_opportunities = 0
    for record in records:
        record_rows = record.terminal_result.world.rebound_opportunity_log
        misses = sum(not shot.get("made") for shot in record.terminal_result.world.shot_attempt_log)
        misses += sum(row["source"] == ReboundSource.FINAL_MISSED_FT for row in record_rows)
        if misses != len(record_rows):
            per_record_mismatches.append(
                f"{record.possession_id}: {misses} reboundable misses vs {len(record_rows)} opportunities"
            )
        keys = Counter((row["step"], row["source"]) for row in record_rows)
        duplicate_opportunities += sum(count - 1 for count in keys.values() if count > 1)

    direct_off = sum(row["outcome"] == ReboundOutcome.SECURED_OFFENSE for row in rows)
    direct_def = sum(row["outcome"] == ReboundOutcome.SECURED_DEFENSE for row in rows)
    team_off = sum(row["outcome"] == ReboundOutcome.TEAM_REBOUND_OFFENSE for row in rows)
    team_def = sum(row["outcome"] == ReboundOutcome.TEAM_REBOUND_DEFENSE for row in rows)
    offensive = direct_off + team_off
    defensive = direct_def + team_def

    stat_off = sum(record.provisional_deltas.oreb for record in records)
    stat_def = sum(record.provisional_deltas.dreb for record in records)
    event_off = sum(event.event_type == EventType.OFFENSIVE_REBOUND
                    for record in records for event in record.events)
    event_def = sum(event.event_type == EventType.DEFENSIVE_REBOUND
                    for record in records for event in record.events)
    accounting_mismatches = list(per_record_mismatches)
    if stat_off != offensive:
        accounting_mismatches.append(f"StatDelta OREB {stat_off} != outcomes {offensive}")
    if stat_def != defensive:
        accounting_mismatches.append(f"StatDelta DREB {stat_def} != outcomes {defensive}")
    if event_off != direct_off:
        accounting_mismatches.append(f"OREB events {event_off} != direct outcomes {direct_off}")
    if event_def != direct_def:
        accounting_mismatches.append(f"DREB events {event_def} != direct outcomes {direct_def}")

    by_family_rows: Dict[str, list] = {}
    for row in rows:
        by_family_rows.setdefault(row["shot_family"], []).append(row)

    contested = [row for row in rows
                 if row["eligible_offensive_count"] > 0 and row["eligible_defensive_count"] > 0]
    off_contested = sum(row["outcome"] in {
        ReboundOutcome.SECURED_OFFENSE, ReboundOutcome.TEAM_REBOUND_OFFENSE,
    } for row in contested)
    def_contested = sum(row["outcome"] in {
        ReboundOutcome.SECURED_DEFENSE, ReboundOutcome.TEAM_REBOUND_DEFENSE,
    } for row in contested)
    expected_off = (sum(_expected_offense_probability(row) for row in contested) / len(contested)
                    if contested else 0.0)

    oreb_counts = [record.provisional_deltas.oreb for record in records]
    total_fga = sum(record.provisional_deltas.fga for record in records)
    fga_after_oreb = sum(
        shot.get("stage_origin") == "SECOND_CHANCE_RESET"
        for record in records for shot in record.terminal_result.world.shot_attempt_log
    )

    return ReboundDiagnosis(
        games=len(results), possessions=len(records), field_goal_misses=field_goal_misses,
        final_missed_free_throw_opportunities=final_ft, reboundable_misses=reboundable_misses,
        rebound_opportunities=len(rows), duplicate_opportunities=duplicate_opportunities,
        misses_without_opportunity=max(0, reboundable_misses - len(rows)),
        opportunities_without_miss=max(0, len(rows) - reboundable_misses),
        offensive_rebounds=offensive, defensive_rebounds=defensive,
        direct_offensive_rebounds=direct_off, direct_defensive_rebounds=direct_def,
        team_offensive_rebounds=team_off, team_defensive_rebounds=team_def,
        unresolved_rebound_outcomes=sum(row["outcome"] not in {
            ReboundOutcome.SECURED_OFFENSE, ReboundOutcome.SECURED_DEFENSE,
            ReboundOutcome.TEAM_REBOUND_OFFENSE, ReboundOutcome.TEAM_REBOUND_DEFENSE,
        } for row in rows),
        rebound_opportunities_beginning_loose=sum(bool(row["resolved_from_loose_state"]) for row in rows),
        by_family={family: _family_stats(family_rows) for family, family_rows in by_family_rows.items()},
        by_zone=dict(Counter(row["rebound_zone"] for row in rows)),
        source_counts=dict(Counter(row["source"] for row in rows)),
        mean_eligible_offense=(sum(row["eligible_offensive_count"] for row in rows) / len(rows) if rows else 0.0),
        mean_eligible_defense=(sum(row["eligible_defensive_count"] for row in rows) / len(rows) if rows else 0.0),
        offense_eligible_opportunities=sum(row["eligible_offensive_count"] > 0 for row in rows),
        defense_eligible_opportunities=sum(row["eligible_defensive_count"] > 0 for row in rows),
        contested_opportunities=len(contested), offense_contested_wins=off_contested,
        defense_contested_wins=def_contested, expected_offense_contested_win_rate=expected_off,
        zero_eligible_opportunities=sum(row["eligible_count"] == 0 for row in rows),
        all_five_offense_eligible=sum(row["eligible_offensive_count"] == 5 for row in rows),
        all_five_defense_eligible=sum(row["eligible_defensive_count"] == 5 for row in rows),
        offense_leverage_opportunities=sum(any(
            candidate["side"] == "OFFENSE" and candidate["box_out_state"] == BoxOutState.ESTABLISHED_BOXOUT
            for candidate in row["candidates"] if candidate["eligible"]
        ) for row in rows),
        defense_leverage_opportunities=sum(any(
            candidate["side"] == "DEFENSE" and candidate["box_out_state"] == BoxOutState.ESTABLISHED_BOXOUT
            for candidate in row["candidates"] if candidate["eligible"]
        ) for row in rows),
        neutral_leverage_opportunities=sum(all(
            candidate["box_out_state"] == BoxOutState.NONE and candidate["boxed_out_by"] is None
            for candidate in row["candidates"] if candidate["eligible"]
        ) for row in rows),
        advantage_present_but_unused=sum(bool(row["advantage_present_but_not_consumed"]) for row in rows),
        possessions_with_one_oreb=sum(count == 1 for count in oreb_counts),
        possessions_with_two_oreb=sum(count == 2 for count in oreb_counts),
        possessions_with_three_plus_oreb=sum(count >= 3 for count in oreb_counts),
        possessions_with_repeat_oreb=sum(count >= 2 for count in oreb_counts),
        repeat_orebs_beyond_first=sum(max(0, count - 1) for count in oreb_counts),
        maximum_oreb_chain=max(oreb_counts, default=0), fga_after_oreb=fga_after_oreb,
        total_fga=total_fga, event_offensive_rebounds=event_off,
        event_defensive_rebounds=event_def,
        accounting_mismatches=tuple(accounting_mismatches),
    )
