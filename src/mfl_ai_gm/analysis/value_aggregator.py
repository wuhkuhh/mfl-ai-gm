"""
Layer 2 — Analysis
Value aggregator — combines FantasyCalc and DynastyProcess dynasty values
into a single normalized consensus score per player.

Methodology:
  Normalization: rank-position based (not value-based).
    Score = 100 * (1 - (rank - 1) / (N - 1))
    #1 ranked player = 100.0, last = 0.0
    This avoids value-clustering collapse (many players at same raw value).

  Sources:
    1. FantasyCalc overall_rank → fc_score (0–100)
    2. DynastyProcess value_1qb rank → dp_score (0–100)
    3. DynastyProcess ecr_1qb → ecr_score (0–100, lower ECR = higher score)

  Consensus = weighted average of available signals:
    - If both FC + DP present: 45% FC + 45% DP + 10% ECR (when available)
    - If FC only: 100% FC score
    - If DP only: 90% DP + 10% ECR (when available)

  Disagreement = abs(fc_score - dp_score) when both present.
  >15 points = "Disputed". FC higher vs DP higher flagged as trade signal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DISAGREEMENT_THRESHOLD = 15.0
MIN_FC_VALUE = 500
MIN_DP_VALUE = 500
ECR_MAX_RANK = 300


@dataclass
class AggregatedPlayerValue:
    mfl_id: str
    name: str
    position: str
    nfl_team: str
    age: Optional[float]

    fc_value: Optional[int] = None
    dp_value: Optional[int] = None
    dp_ecr: Optional[float] = None

    fc_score: Optional[float] = None
    dp_score: Optional[float] = None
    ecr_score: Optional[float] = None

    consensus_score: float = 0.0
    consensus_rank: int = 0
    sources: int = 0

    disagreement: float = 0.0
    is_disputed: bool = False
    value_signal: str = "Consensus"

    fc_rank: Optional[int] = None
    fc_trend_30d: Optional[int] = None
    fc_tier: Optional[int] = None
    fc_position_rank: Optional[int] = None

    dp_ecr_pos: Optional[float] = None
    dp_scrape_date: Optional[str] = None
    dp_draft_year: Optional[int] = None


def _rank_score(rank: int, n: int) -> float:
    """Rank 1 → 100.0, rank N → 0.0."""
    if n <= 1:
        return 100.0
    return round(100.0 * (1.0 - (rank - 1) / (n - 1)), 2)


def _ecr_score(ecr: float, ecr_max: float = ECR_MAX_RANK) -> float:
    """ECR 1 → 100.0, ECR ecr_max → 0.0."""
    if ecr <= 0:
        return 100.0
    return round(max(0.0, 100.0 * (1.0 - (ecr - 1) / (ecr_max - 1))), 2)


def build_consensus_values(
    fc_map: dict,
    dp_map: dict,
) -> list[AggregatedPlayerValue]:
    """
    Combine FC and DP into consensus rankings.
    Returns list sorted by consensus_score descending.
    Excludes draft picks, DEF, K.
    """
    SKIP_POS = {"PICK", "DEF", "K"}

    fc_eligible = [
        p for p in sorted(fc_map.values(), key=lambda x: x.overall_rank)
        if p.value >= MIN_FC_VALUE and p.position not in SKIP_POS and p.mfl_id
    ]
    dp_eligible = [
        p for p in sorted(dp_map.values(), key=lambda x: x.value_1qb, reverse=True)
        if p.value_1qb >= MIN_DP_VALUE and p.position not in SKIP_POS and p.mfl_id
    ]

    fc_n = len(fc_eligible)
    dp_n = len(dp_eligible)
    logger.info("Aggregating: %d FC eligible, %d DP eligible", fc_n, dp_n)

    fc_scores: dict[str, tuple] = {
        p.mfl_id: (_rank_score(rank, fc_n), p)
        for rank, p in enumerate(fc_eligible, 1)
    }
    dp_scores: dict[str, tuple] = {
        p.mfl_id: (_rank_score(rank, dp_n), p)
        for rank, p in enumerate(dp_eligible, 1)
    }

    all_mfl_ids = set(fc_scores.keys()) | set(dp_scores.keys())
    players: list[AggregatedPlayerValue] = []

    for mfl_id in all_mfl_ids:
        fc_entry = fc_scores.get(mfl_id)
        dp_entry = dp_scores.get(mfl_id)

        if fc_entry:
            fc_s, fc_p = fc_entry
            name, position, nfl_team, age = fc_p.name, fc_p.position, fc_p.nfl_team, fc_p.age
        else:
            fc_s = None
            _, dp_p0 = dp_entry
            name, position, nfl_team, age = dp_p0.name, dp_p0.position, dp_p0.nfl_team, dp_p0.age

        agg = AggregatedPlayerValue(
            mfl_id=mfl_id, name=name, position=position, nfl_team=nfl_team, age=age
        )

        if fc_entry:
            fc_s, fc_p = fc_entry
            agg.fc_score = fc_s
            agg.fc_value = fc_p.value
            agg.fc_rank = fc_p.overall_rank
            agg.fc_position_rank = fc_p.position_rank
            agg.fc_trend_30d = fc_p.trend_30d
            agg.fc_tier = fc_p.tier

        if dp_entry:
            dp_s, dp_p = dp_entry
            agg.dp_score = dp_s
            agg.dp_value = dp_p.value_1qb
            agg.dp_ecr = dp_p.ecr_1qb
            agg.dp_ecr_pos = dp_p.ecr_pos
            agg.dp_scrape_date = dp_p.scrape_date
            agg.dp_draft_year = dp_p.draft_year
            if dp_p.ecr_1qb:
                agg.ecr_score = _ecr_score(dp_p.ecr_1qb)

        has_fc = agg.fc_score is not None
        has_dp = agg.dp_score is not None
        has_ecr = agg.ecr_score is not None

        if has_fc and has_dp:
            agg.sources = 2
            agg.consensus_score = round(
                (0.45 * agg.fc_score + 0.45 * agg.dp_score + 0.10 * agg.ecr_score)
                if has_ecr else
                (0.50 * agg.fc_score + 0.50 * agg.dp_score),
                2
            )
        elif has_fc:
            agg.sources = 1
            agg.consensus_score = round(agg.fc_score, 2)
        elif has_dp:
            agg.sources = 1
            agg.consensus_score = round(
                (0.90 * agg.dp_score + 0.10 * agg.ecr_score) if has_ecr else agg.dp_score,
                2
            )

        if has_fc and has_dp:
            agg.disagreement = round(abs(agg.fc_score - agg.dp_score), 2)
            agg.is_disputed = agg.disagreement > DISAGREEMENT_THRESHOLD
            if agg.fc_score > agg.dp_score + DISAGREEMENT_THRESHOLD:
                agg.value_signal = "FC Higher"
            elif agg.dp_score > agg.fc_score + DISAGREEMENT_THRESHOLD:
                agg.value_signal = "DP Higher"

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
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from mfl_ai_gm.adapters.fantasycalc_client import fetch_fc_values, build_mfl_value_map as fc_mfl_map
    from mfl_ai_gm.adapters.dynastyprocess_client import fetch_dp_values, build_dp_mfl_map

    force = "--force" in sys.argv
    fc_map = fc_mfl_map(fetch_fc_values(force_refresh=force))
    dp_map = build_dp_mfl_map(fetch_dp_values(force_refresh=force))
    consensus = build_consensus_values(fc_map, dp_map)

    print(f"\n{'#':<5} {'Player':<28} {'Pos':<5} {'Score':<7} {'FC':<7} {'DP':<7} {'ECR':<7} {'Disp':<6} Signal")
    print("-" * 85)
    for p in consensus[:40]:
        fc_s = f"{p.fc_score:.1f}" if p.fc_score is not None else "—"
        dp_s = f"{p.dp_score:.1f}" if p.dp_score is not None else "—"
        ecr_s = f"{p.ecr_score:.1f}" if p.ecr_score is not None else "—"
        disp = f"{p.disagreement:.1f}" if p.sources == 2 else "—"
        flag = " ⚠" if p.is_disputed else ""
        print(f"{p.consensus_rank:<5} {p.name:<28} {p.position:<5} {p.consensus_score:<7.1f} {fc_s:<7} {dp_s:<7} {ecr_s:<7} {disp:<6} {p.value_signal}{flag}")

    disputed = [p for p in consensus if p.is_disputed]
    if disputed:
        print(f"\n--- Disputed ({len(disputed)} total, top by gap) ---")
        for p in sorted(disputed, key=lambda x: x.disagreement, reverse=True)[:15]:
            print(f"  {p.name:<28} {p.position:<4} FC={p.fc_score:.1f} DP={p.dp_score:.1f} gap={p.disagreement:.1f} → {p.value_signal}")

    print(f"\nTotal: {len(consensus)} | Both: {sum(1 for p in consensus if p.sources==2)} | FC only: {sum(1 for p in consensus if p.sources==1 and p.fc_score is not None)} | Disputed: {len(disputed)}")
