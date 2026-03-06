"""
Layer 2 — Analysis
Value aggregator — combines FantasyCalc and DynastyProcess dynasty values
into a single normalized consensus score per player.

Methodology:
  1. Normalize each source's raw value to 0–100 using min-max against the
     top-N players in that source (N=200 to avoid outlier compression).
  2. Consensus score = simple average of available normalized scores.
  3. Source disagreement = abs(fc_norm - dp_norm). >15 = "Disputed".
  4. ECR from DynastyProcess used as a third signal (not averaged in,
     but surfaced as a ranking check).

Output: AggregatedPlayerValue per player keyed by MFL ID.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Use top-N players as the normalization ceiling (prevents outlier compression)
NORM_TOP_N = 200

# Disagreement threshold between sources (0-100 scale)
DISAGREEMENT_THRESHOLD = 15.0

# Minimum raw value to include a player (filters fringe/practice squad)
MIN_FC_VALUE = 500
MIN_DP_VALUE = 500


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class AggregatedPlayerValue:
    mfl_id: str
    name: str
    position: str
    nfl_team: str
    age: Optional[float]

    # Raw values from each source
    fc_value: Optional[int] = None           # FantasyCalc raw (0–10000+)
    dp_value: Optional[int] = None           # DynastyProcess value_1qb (0–10000+)

    # Normalized scores (0–100)
    fc_norm: Optional[float] = None
    dp_norm: Optional[float] = None

    # Consensus
    consensus_score: float = 0.0             # Average of available normalized scores
    consensus_rank: int = 0                  # 1 = best, among all aggregated players
    sources: int = 0                         # How many sources contributed

    # Disagreement
    disagreement: float = 0.0               # abs(fc_norm - dp_norm), 0 if one source
    is_disputed: bool = False               # True if disagreement > threshold

    # FantasyCalc extras
    fc_rank: Optional[int] = None
    fc_trend_30d: Optional[int] = None
    fc_tier: Optional[int] = None

    # DynastyProcess extras
    dp_ecr_1qb: Optional[float] = None      # Expert consensus rank (lower = better)
    dp_scrape_date: Optional[str] = None

    # Directional signal when sources disagree
    value_signal: str = "Consensus"          # "FC Higher", "DP Higher", or "Consensus"


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------

def _normalize(values: list[int], top_n: int = NORM_TOP_N) -> dict[int, float]:
    """
    Map a list of raw values to 0–100 using the top_n ceiling.
    Returns dict: raw_value → normalized_score.
    """
    if not values:
        return {}
    sorted_vals = sorted(values, reverse=True)
    ceiling = sorted_vals[min(top_n - 1, len(sorted_vals) - 1)]
    floor = 0
    span = ceiling - floor
    if span == 0:
        return {v: 100.0 for v in values}
    result = {}
    for v in values:
        norm = max(0.0, min(100.0, (v - floor) / span * 100))
        result[v] = round(norm, 2)
    return result


# ---------------------------------------------------------------------------
# Main aggregator
# ---------------------------------------------------------------------------

def build_consensus_values(
    fc_map: dict,       # mfl_id → FCPlayerValue
    dp_map: dict,       # mfl_id → DPPlayerValue
) -> list[AggregatedPlayerValue]:
    """
    Combine FantasyCalc and DynastyProcess values into consensus rankings.
    Returns list sorted by consensus_score descending (rank 1 = best).
    """
    # Collect all MFL IDs present in either source
    all_mfl_ids = set(fc_map.keys()) | set(dp_map.keys())
    logger.info(
        "Aggregating: %d FC players, %d DP players, %d unique MFL IDs",
        len(fc_map), len(dp_map), len(all_mfl_ids),
    )

    # Build normalization maps
    fc_values = [p.value for p in fc_map.values() if p.value >= MIN_FC_VALUE]
    dp_values = [p.value_1qb for p in dp_map.values() if p.value_1qb >= MIN_DP_VALUE]
    fc_norm_map = _normalize(fc_values)
    dp_norm_map = _normalize(dp_values)

    players: list[AggregatedPlayerValue] = []

    for mfl_id in all_mfl_ids:
        fc = fc_map.get(mfl_id)
        dp = dp_map.get(mfl_id)

        # Determine canonical name/pos/team/age (prefer FC, fall back to DP)
        if fc:
            name = fc.name
            position = fc.position
            nfl_team = fc.nfl_team
            age = fc.age
        else:
            name = dp.name if dp else ""
            position = dp.position if dp else ""
            nfl_team = dp.nfl_team if dp else "FA"
            age = dp.age if dp else None

        agg = AggregatedPlayerValue(
            mfl_id=mfl_id,
            name=name,
            position=position,
            nfl_team=nfl_team,
            age=age,
        )

        # FC data
        if fc and fc.value >= MIN_FC_VALUE:
            agg.fc_value = fc.value
            agg.fc_norm = fc_norm_map.get(fc.value, 0.0)
            agg.fc_rank = fc.overall_rank
            agg.fc_trend_30d = fc.trend_30d
            agg.fc_tier = fc.tier

        # DP data
        if dp and dp.value_1qb >= MIN_DP_VALUE:
            agg.dp_value = dp.value_1qb
            agg.dp_norm = dp_norm_map.get(dp.value_1qb, 0.0)
            agg.dp_ecr_1qb = dp.ecr_1qb
            agg.dp_scrape_date = dp.scrape_date

        # Consensus score
        scores = [s for s in [agg.fc_norm, agg.dp_norm] if s is not None]
        agg.sources = len(scores)
        agg.consensus_score = round(sum(scores) / len(scores), 2) if scores else 0.0

        # Disagreement
        if agg.fc_norm is not None and agg.dp_norm is not None:
            agg.disagreement = round(abs(agg.fc_norm - agg.dp_norm), 2)
            agg.is_disputed = agg.disagreement > DISAGREEMENT_THRESHOLD
            if agg.fc_norm > agg.dp_norm + DISAGREEMENT_THRESHOLD:
                agg.value_signal = "FC Higher"
            elif agg.dp_norm > agg.fc_norm + DISAGREEMENT_THRESHOLD:
                agg.value_signal = "DP Higher"
            else:
                agg.value_signal = "Consensus"

        if agg.consensus_score > 0:
            players.append(agg)

    # Sort and assign ranks
    players.sort(key=lambda p: p.consensus_score, reverse=True)
    for i, p in enumerate(players, 1):
        p.consensus_rank = i

    disputed = sum(1 for p in players if p.is_disputed)
    both_sources = sum(1 for p in players if p.sources == 2)
    logger.info(
        "Consensus: %d players ranked, %d with both sources, %d disputed",
        len(players), both_sources, disputed,
    )
    return players


def build_consensus_mfl_map(
    players: list[AggregatedPlayerValue],
) -> dict[str, AggregatedPlayerValue]:
    """Build dict of mfl_id → AggregatedPlayerValue."""
    return {p.mfl_id: p for p in players}


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from mfl_ai_gm.adapters.fantasycalc_client import fetch_fc_values, build_mfl_value_map as fc_mfl_map
    from mfl_ai_gm.adapters.dynastyprocess_client import fetch_dp_values, build_dp_mfl_map

    force = "--force" in sys.argv
    fc_players = fetch_fc_values(force_refresh=force)
    dp_players = fetch_dp_values(force_refresh=force)

    fc_map = fc_mfl_map(fc_players)
    dp_map = build_dp_mfl_map(dp_players)

    consensus = build_consensus_values(fc_map, dp_map)

    print(f"\n{'#':<5} {'Player':<28} {'Pos':<5} {'Score':<8} {'FC':<8} {'DP':<8} {'Disp':<6} Signal")
    print("-" * 80)
    for p in consensus[:40]:
        fc_s = f"{p.fc_norm:.1f}" if p.fc_norm is not None else "—"
        dp_s = f"{p.dp_norm:.1f}" if p.dp_norm is not None else "—"
        disp = f"{p.disagreement:.1f}" if p.sources == 2 else "—"
        flag = "⚠" if p.is_disputed else ""
        print(
            f"{p.consensus_rank:<5} {p.name:<28} {p.position:<5} "
            f"{p.consensus_score:<8.1f} {fc_s:<8} {dp_s:<8} {disp:<6} {p.value_signal} {flag}"
        )

    disputed = [p for p in consensus if p.is_disputed]
    print(f"\n--- Disputed Values ({len(disputed)} players) ---")
    for p in disputed[:15]:
        print(
            f"  {p.name:<28} {p.position:<4} FC={p.fc_norm:.1f} DP={p.dp_norm:.1f} "
            f"gap={p.disagreement:.1f} → {p.value_signal}"
        )

    print(f"\nTotal ranked: {len(consensus)}")
    print(f"Both sources: {sum(1 for p in consensus if p.sources == 2)}")
    print(f"FC only:      {sum(1 for p in consensus if p.sources == 1 and p.fc_norm is not None)}")
    print(f"DP only:      {sum(1 for p in consensus if p.sources == 1 and p.dp_norm is not None)}")
