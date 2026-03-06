"""
Layer 2 — Analysis
Value aggregator — combines FantasyCalc and DynastyProcess dynasty values
into a single normalized consensus score per player.

Methodology:
  1. Normalize each source's raw value to 0–100 using top-200 ceiling.
  2. Consensus score = simple average of available normalized scores.
  3. Source disagreement = abs(fc_norm - dp_norm). >15 = "Disputed".
  4. ECR from DynastyProcess surfaced as a third signal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

NORM_TOP_N = 200
DISAGREEMENT_THRESHOLD = 15.0
MIN_FC_VALUE = 500
MIN_DP_VALUE = 500


@dataclass
class AggregatedPlayerValue:
    mfl_id: str
    name: str
    position: str
    nfl_team: str
    age: Optional[float]
    fc_value: Optional[int] = None
    dp_value: Optional[int] = None
    fc_norm: Optional[float] = None
    dp_norm: Optional[float] = None
    consensus_score: float = 0.0
    consensus_rank: int = 0
    sources: int = 0
    disagreement: float = 0.0
    is_disputed: bool = False
    fc_rank: Optional[int] = None
    fc_trend_30d: Optional[int] = None
    fc_tier: Optional[int] = None
    dp_ecr_1qb: Optional[float] = None
    dp_scrape_date: Optional[str] = None
    ktc_value: Optional[int] = None
    ktc_norm: Optional[float] = None
    ktc_rank: Optional[int] = None
    ktc_trend: Optional[int] = None
    value_signal: str = "Consensus"


def _normalize(values: list[int], top_n: int = NORM_TOP_N) -> dict[int, float]:
    if not values:
        return {}
    sorted_vals = sorted(values, reverse=True)
    ceiling = sorted_vals[0]
    span = ceiling or 1.0
    result = {}
    for v in values:
        norm = max(0.0, min(100.0, v / span * 100))
        result[v] = round(norm, 2)
    return result
def build_consensus_values(fc_map: dict, dp_map: dict, ktc_map: dict = None) -> list[AggregatedPlayerValue]:
    ktc_map = ktc_map or {}
    all_mfl_ids = set(fc_map.keys()) | set(dp_map.keys()) | set(ktc_map.keys())
    logger.info("Aggregating: %d FC, %d DP, %d KTC, %d unique MFL IDs",
                len(fc_map), len(dp_map), len(ktc_map), len(all_mfl_ids))

    fc_values = [p.value for p in fc_map.values() if p.value >= MIN_FC_VALUE]
    dp_values = [p.value_1qb for p in dp_map.values() if p.value_1qb >= MIN_DP_VALUE]
    ktc_values = [p.value for p in ktc_map.values() if p.value >= 100]
    fc_norm_map = _normalize(fc_values)
    dp_norm_map = _normalize(dp_values)
    ktc_norm_map = _normalize(ktc_values)

    players: list[AggregatedPlayerValue] = []
    for mfl_id in all_mfl_ids:
        fc = fc_map.get(mfl_id)
        dp = dp_map.get(mfl_id)
        ktc = ktc_map.get(mfl_id)
        if ktc:
            name, position, nfl_team, age = ktc.name, ktc.position, ktc.nfl_team, ktc.age
        elif fc:
            name, position, nfl_team, age = fc.name, fc.position, fc.nfl_team, fc.age
        else:
            name = dp.name if dp else ""
            position = dp.position if dp else ""
            nfl_team = dp.nfl_team if dp else "FA"
            age = dp.age if dp else None

        agg = AggregatedPlayerValue(mfl_id=mfl_id, name=name, position=position,
                                    nfl_team=nfl_team, age=age)
        if fc and fc.value >= MIN_FC_VALUE:
            agg.fc_value = fc.value
            agg.fc_norm = fc_norm_map.get(fc.value, 0.0)
            agg.fc_rank = fc.overall_rank
            agg.fc_trend_30d = fc.trend_30d
            agg.fc_tier = fc.tier
        if dp and dp.value_1qb >= MIN_DP_VALUE:
            agg.dp_value = dp.value_1qb
            agg.dp_norm = dp_norm_map.get(dp.value_1qb, 0.0)
            agg.dp_ecr_1qb = dp.ecr_1qb
            agg.dp_scrape_date = dp.scrape_date
        if ktc and ktc.value >= 100:
            agg.ktc_value = ktc.value
            agg.ktc_norm = ktc_norm_map.get(ktc.value, 0.0)
            agg.ktc_rank = ktc.rank
            agg.ktc_trend = ktc.trend_overall

        scores = [s for s in [agg.fc_norm, agg.dp_norm, agg.ktc_norm] if s is not None]
        agg.sources = len(scores)
        agg.consensus_score = round(sum(scores) / len(scores), 2) if scores else 0.0

        available = [s for s in [agg.fc_norm, agg.dp_norm, agg.ktc_norm] if s is not None]
        if len(available) >= 2:
            agg.disagreement = round(max(available) - min(available), 2)
            agg.is_disputed = agg.disagreement > DISAGREEMENT_THRESHOLD
            if agg.ktc_norm is not None and agg.fc_norm is not None:
                if agg.ktc_norm > agg.fc_norm + DISAGREEMENT_THRESHOLD:
                    agg.value_signal = "KTC Higher"
                elif agg.fc_norm > agg.ktc_norm + DISAGREEMENT_THRESHOLD:
                    agg.value_signal = "FC Higher"
                elif agg.dp_norm is not None and agg.dp_norm > agg.fc_norm + DISAGREEMENT_THRESHOLD:
                    agg.value_signal = "DP Higher"
                else:
                    agg.value_signal = "Consensus"
            elif agg.fc_norm is not None and agg.dp_norm is not None:
                if agg.fc_norm > agg.dp_norm + DISAGREEMENT_THRESHOLD:
                    agg.value_signal = "FC Higher"
                elif agg.dp_norm > agg.fc_norm + DISAGREEMENT_THRESHOLD:
                    agg.value_signal = "DP Higher"
                else:
                    agg.value_signal = "Consensus"

        if agg.consensus_score > 0:
            players.append(agg)

    players.sort(key=lambda p: p.consensus_score, reverse=True)
    for i, p in enumerate(players, 1):
        p.consensus_rank = i

    disputed = sum(1 for p in players if p.is_disputed)
    both = sum(1 for p in players if p.sources == 2)
    logger.info("Consensus: %d ranked, %d both sources, %d disputed", len(players), both, disputed)
    return players


def build_consensus_mfl_map(players: list[AggregatedPlayerValue]) -> dict[str, AggregatedPlayerValue]:
    return {p.mfl_id: p for p in players}


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import sys
    from mfl_ai_gm.adapters.fantasycalc_client import fetch_fc_values, build_mfl_value_map as fc_mfl_map
    from mfl_ai_gm.adapters.dynastyprocess_client import fetch_dp_values, build_dp_mfl_map
    force = "--force" in sys.argv
    fc_map = fc_mfl_map(fetch_fc_values(force_refresh=force))
    dp_map = build_dp_mfl_map(fetch_dp_values(force_refresh=force))
    consensus = build_consensus_values(fc_map, dp_map)
    print(f"\nTop 20 Consensus:")
    print(f"{'#':<5} {'Player':<28} {'Pos':<5} {'Score':<8} {'FC':<8} {'DP':<8} Signal")
    print("-" * 70)
    for p in consensus[:20]:
        fc_s = f"{p.fc_norm:.1f}" if p.fc_norm is not None else "—"
        dp_s = f"{p.dp_norm:.1f}" if p.dp_norm is not None else "—"
        print(f"{p.consensus_rank:<5} {p.name:<28} {p.position:<5} {p.consensus_score:<8.1f} {fc_s:<8} {dp_s:<8} {p.value_signal}")
    print(f"\nTotal: {len(consensus)} | Both sources: {sum(1 for p in consensus if p.sources==2)}")
