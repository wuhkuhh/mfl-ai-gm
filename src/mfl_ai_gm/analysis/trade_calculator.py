"""
Layer 2 — Analysis
Dynasty trade calculator — unlimited players + draft picks, consensus value scoring.

Both players AND picks are scored via the consensus map (FC MFL IDs for picks).
Pick MFL IDs follow FC conventions: DP_round_slot (e.g. DP_0_4 = 2026 1.05)
and FP_year_round (e.g. FP_2027_1 = 2027 1st round).

No separate pick table needed — FC already values picks on the same scale as players.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

FAIR_DELTA = 8.0
SLIGHT_DELTA = 18.0
SIGNIFICANT_DELTA = 35.0

# Full pick label → FC mfl_id mapping
# Source: FantasyCalc API, position="PI" players
PICK_LABEL_TO_MFL_ID: dict[str, str] = {
    "2026 Pick 1.01": "DP_0_0",
    "2026 Pick 1.02": "DP_0_1",
    "2026 Pick 1.03": "DP_0_2",
    "2026 Pick 1.04": "DP_0_3",
    "2026 Pick 1.05": "DP_0_4",
    "2026 Pick 1.06": "DP_0_5",
    "2026 Pick 1.07": "DP_0_6",
    "2026 Pick 1.08": "DP_0_7",
    "2026 Pick 1.09": "DP_0_8",
    "2026 Pick 1.10": "DP_0_9",
    "2026 Pick 1.11": "DP_0_10",
    "2026 Pick 1.12": "DP_0_11",
    "2026 Pick 2.01": "DP_1_0",
    "2026 Pick 2.02": "DP_1_1",
    "2026 Pick 2.03": "DP_1_2",
    "2026 Pick 2.04": "DP_1_3",
    "2026 Pick 2.05": "DP_1_4",
    "2026 Pick 2.06": "DP_1_5",
    "2026 Pick 2.07": "DP_1_6",
    "2026 Pick 2.08": "DP_1_7",
    "2026 Pick 2.09": "DP_1_8",
    "2026 Pick 2.10": "DP_1_9",
    "2026 Pick 2.11": "DP_1_10",
    "2026 Pick 2.12": "DP_1_11",
    "2026 Pick 3.01": "DP_2_0",
    "2026 Pick 3.02": "DP_2_1",
    "2026 Pick 3.03": "DP_2_2",
    "2026 Pick 3.04": "DP_2_3",
    "2026 Pick 3.05": "DP_2_4",
    "2026 Pick 3.06": "DP_2_5",
    "2026 Pick 3.07": "DP_2_6",
    "2026 Pick 3.08": "DP_2_7",
    "2026 Pick 3.09": "DP_2_8",
    "2026 Pick 3.10": "DP_2_9",
    "2026 Pick 3.11": "DP_2_10",
    "2026 Pick 3.12": "DP_2_11",
    "2026 Pick 4.01": "DP_3_0",
    "2026 Pick 4.02": "DP_3_1",
    "2026 Pick 4.03": "DP_3_2",
    "2026 Pick 4.04": "DP_3_3",
    "2026 Pick 4.05": "DP_3_4",
    "2026 Pick 4.06": "DP_3_5",
    "2026 Pick 4.07": "DP_3_6",
    "2026 Pick 4.08": "DP_3_7",
    "2026 Pick 4.09": "DP_3_8",
    "2026 Pick 4.10": "DP_3_9",
    "2026 Pick 4.11": "DP_3_10",
    "2026 Pick 4.12": "DP_3_11",
    "2026 1st": "FP_2026_1",
    "2026 2nd": "FP_2026_2",
    "2026 3rd": "FP_2026_3",
    "2027 1st": "FP_2027_1",
    "2027 2nd": "FP_2027_2",
    "2027 3rd": "FP_2027_3",
    "2028 1st": "FP_2028_1",
    "2028 2nd": "FP_2028_2",
    "2028 3rd": "FP_2028_3",
}

# Reverse map for lookup by mfl_id
MFL_ID_TO_PICK_LABEL: dict[str, str] = {v: k for k, v in PICK_LABEL_TO_MFL_ID.items()}


@dataclass
class TradeAsset:
    asset_type: str          # "player" or "pick"
    label: str               # Display name
    mfl_id: Optional[str] = None
    consensus_score: float = 0.0
    fc_score: Optional[float] = None
    dp_score: Optional[float] = None
    sources: int = 0
    position: Optional[str] = None
    nfl_team: Optional[str] = None
    age: Optional[float] = None
    notes: str = ""
    is_disputed: bool = False


@dataclass
class TradeSide:
    assets: list[TradeAsset] = field(default_factory=list)
    total_score: float = 0.0
    player_count: int = 0
    pick_count: int = 0


@dataclass
class TradeVerdict:
    side_a: TradeSide
    side_b: TradeSide
    delta: float = 0.0
    winner: str = "Even"
    fairness: str = "Fair Trade"
    advantage_pct: float = 0.0
    summary: str = ""
    recommendation: str = ""
    disputed_assets: list[str] = field(default_factory=list)


def resolve_pick_mfl_id(label: str) -> Optional[str]:
    """Resolve a pick label to its FC mfl_id. Returns None if not found."""
    return PICK_LABEL_TO_MFL_ID.get(label)


def get_all_picks() -> list[dict]:
    """Return all known picks as label/mfl_id pairs, ordered by value (best first)."""
    # Ordered by approximate value: specific slots first (best to worst), then future rounds
    ordered = [
        "2026 Pick 1.01", "2026 Pick 1.02", "2026 Pick 1.03", "2026 Pick 1.04",
        "2026 Pick 1.05", "2026 Pick 1.06", "2026 Pick 1.07", "2026 Pick 1.08",
        "2026 Pick 1.09", "2026 Pick 1.10", "2026 Pick 1.11", "2026 Pick 1.12",
        "2027 1st", "2026 1st",
        "2026 Pick 2.01", "2026 Pick 2.02", "2026 Pick 2.03", "2026 Pick 2.04",
        "2026 Pick 2.05", "2026 Pick 2.06", "2026 Pick 2.07", "2026 Pick 2.08",
        "2026 Pick 2.09", "2026 Pick 2.10", "2026 Pick 2.11", "2026 Pick 2.12",
        "2027 2nd", "2028 1st", "2026 2nd",
        "2026 Pick 3.01", "2026 Pick 3.02", "2026 Pick 3.03", "2026 Pick 3.04",
        "2026 Pick 3.05", "2026 Pick 3.06", "2026 Pick 3.07", "2026 Pick 3.08",
        "2026 Pick 3.09", "2026 Pick 3.10", "2026 Pick 3.11", "2026 Pick 3.12",
        "2027 3rd", "2028 2nd", "2026 3rd",
        "2026 Pick 4.01", "2026 Pick 4.02", "2026 Pick 4.03", "2026 Pick 4.04",
        "2026 Pick 4.05", "2026 Pick 4.06", "2026 Pick 4.07", "2026 Pick 4.08",
        "2026 Pick 4.09", "2026 Pick 4.10", "2026 Pick 4.11", "2026 Pick 4.12",
        "2028 3rd",
    ]
    return [{"label": lbl, "mfl_id": PICK_LABEL_TO_MFL_ID[lbl]}
            for lbl in ordered if lbl in PICK_LABEL_TO_MFL_ID]


def _score_asset(asset: TradeAsset, consensus_map: dict) -> list[str]:
    """Score any asset (player or pick) via consensus map. Returns disputed flags."""
    disputed = []
    if not asset.mfl_id:
        asset.notes = "no MFL ID — unscored"
        return [f"{asset.label}: no MFL ID"]

    agg = consensus_map.get(asset.mfl_id)
    if not agg:
        asset.notes = "not in value map"
        return [f"{asset.label}: not in consensus map"]

    asset.consensus_score = agg.consensus_score
    asset.fc_score = agg.fc_norm
    asset.dp_score = agg.dp_norm
    asset.sources = agg.sources
    asset.position = asset.position or agg.position
    asset.nfl_team = asset.nfl_team or agg.nfl_team
    asset.age = asset.age or agg.age
    asset.is_disputed = agg.is_disputed

    if agg.is_disputed:
        disputed.append(
            f"{asset.label}: FC={agg.fc_norm:.0f} vs DP={agg.dp_norm:.0f} "
            f"(Δ{agg.disagreement:.0f}) — {agg.value_signal}"
        )
    return disputed


def evaluate_trade(
    side_a_assets: list[TradeAsset],
    side_b_assets: list[TradeAsset],
    consensus_map: dict,
) -> TradeVerdict:
    """
    Evaluate a dynasty trade. Both players and picks scored via consensus map.

    Args:
        side_a_assets: Assets Team A receives
        side_b_assets: Assets Team B receives
        consensus_map: mfl_id → AggregatedPlayerValue (includes picks)

    Returns:
        TradeVerdict with full scoring breakdown
    """
    disputed_flags: list[str] = []

    def _score_side(assets: list[TradeAsset]) -> TradeSide:
        side = TradeSide(assets=assets)
        for asset in assets:
            flags = _score_asset(asset, consensus_map)
            disputed_flags.extend(flags)
            side.total_score += asset.consensus_score
            if asset.asset_type == "pick":
                side.pick_count += 1
            else:
                side.player_count += 1
        side.total_score = round(side.total_score, 2)
        return side

    side_a = _score_side(side_a_assets)
    side_b = _score_side(side_b_assets)

    delta = round(side_a.total_score - side_b.total_score, 2)
    abs_delta = abs(delta)

    if abs_delta <= FAIR_DELTA:
        winner, fairness = "Even", "Fair Trade"
    elif abs_delta <= SLIGHT_DELTA:
        winner = "Side A" if delta > 0 else "Side B"
        fairness = "Slight Advantage"
    elif abs_delta <= SIGNIFICANT_DELTA:
        winner = "Side A" if delta > 0 else "Side B"
        fairness = "Significant Advantage"
    else:
        winner = "Side A" if delta > 0 else "Side B"
        fairness = "Lopsided"

    total = side_a.total_score + side_b.total_score
    adv_pct = round((abs_delta / total * 100) if total > 0 else 0.0, 1)

    if winner == "Even":
        summary = f"Fair trade — both sides within {FAIR_DELTA:.0f} points of each other."
    else:
        winning = side_a if winner == "Side A" else side_b
        top = max(winning.assets, key=lambda a: a.consensus_score, default=None)
        summary = (
            f"{winner} wins by {abs_delta:.1f} pts ({adv_pct:.1f}% advantage)."
            + (f" Driven by {top.label}." if top else "")
        )

    rec_map = {
        "Fair Trade": "Pull the trigger — this is a fair deal.",
        "Slight Advantage": f"{'Side B' if winner == 'Side A' else 'Side A'} should ask for a small sweetener.",
        "Significant Advantage": f"{'Side B' if winner == 'Side A' else 'Side A'} is losing this trade — counter with an addition.",
        "Lopsided": f"{'Side B' if winner == 'Side A' else 'Side A'} should decline or heavily renegotiate.",
    }

    return TradeVerdict(
        side_a=side_a, side_b=side_b, delta=delta, winner=winner,
        fairness=fairness, advantage_pct=adv_pct,
        summary=summary, recommendation=rec_map[fairness],
        disputed_assets=disputed_flags,
    )


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from mfl_ai_gm.adapters.fantasycalc_client import fetch_fc_values, build_mfl_value_map
    from mfl_ai_gm.adapters.dynastyprocess_client import fetch_dp_values, build_dp_mfl_map
    from mfl_ai_gm.analysis.value_aggregator import build_consensus_values, build_consensus_mfl_map

    fc_map = build_mfl_value_map(fetch_fc_values())
    dp_map = build_dp_mfl_map(fetch_dp_values())
    cmap = build_consensus_mfl_map(build_consensus_values(fc_map, dp_map))

    # Test 1: Bijan Robinson FOR James Cook + 2026 1.05
    side_a = [TradeAsset(asset_type="player", label="Bijan Robinson", mfl_id="16161")]
    side_b = [
        TradeAsset(asset_type="player", label="James Cook", mfl_id="15715"),
        TradeAsset(asset_type="pick", label="2026 Pick 1.05",
                   mfl_id=resolve_pick_mfl_id("2026 Pick 1.05")),
    ]
    v = evaluate_trade(side_a, side_b, cmap)
    print(f"\n{'='*55}\nTest 1: Bijan vs Cook + 1.05\n{'='*55}")
    print(f"\nSide A — {v.side_a.total_score:.1f} pts:")
    for a in v.side_a.assets:
        print(f"  {a.label:<32} {a.consensus_score:.1f}")
    print(f"\nSide B — {v.side_b.total_score:.1f} pts:")
    for a in v.side_b.assets:
        print(f"  {a.label:<32} {a.consensus_score:.1f}  {a.notes or ''}")
    print(f"\nVerdict: {v.fairness} — {v.winner}")
    print(f"Delta:   {v.delta:+.1f} pts  ({v.advantage_pct:.1f}%)")
    print(f"\n{v.summary}")
    print(f"→ {v.recommendation}")

    # Test 2: 2-for-1 — JSN + 2027 1st FOR Bijan
    side_a2 = [
        TradeAsset(asset_type="player", label="Jaxon Smith-Njigba", mfl_id="16804"),
        TradeAsset(asset_type="pick", label="2027 1st",
                   mfl_id=resolve_pick_mfl_id("2027 1st")),
    ]
    side_b2 = [TradeAsset(asset_type="player", label="Bijan Robinson", mfl_id="16161")]
    v2 = evaluate_trade(side_a2, side_b2, cmap)
    print(f"\n{'='*55}\nTest 2: JSN + 2027 1st vs Bijan\n{'='*55}")
    print(f"Side A: {v2.side_a.total_score:.1f}  Side B: {v2.side_b.total_score:.1f}")
    print(f"Verdict: {v2.fairness} — {v2.winner}  (Δ{v2.delta:+.1f})")
    print(f"→ {v2.recommendation}")
