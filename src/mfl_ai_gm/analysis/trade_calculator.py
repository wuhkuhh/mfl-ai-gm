"""
Layer 2 — Analysis
Dynasty trade calculator — unlimited players + draft picks, consensus value scoring.

Pick slot format: round=1, slot=5 → "2026 1.05"
Player values: consensus aggregator (FC + DP normalized 0-100)
Pick values: DP values-picks.csv (ecr_1qb inverted + year penalty)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

FAIR_DELTA = 8.0
SLIGHT_DELTA = 18.0
SIGNIFICANT_DELTA = 35.0
MIN_PLAYER_SCORE = 1.0

# Baseline pick values (round, slot) → 0-100 normalized
# Used if DP picks not available
BASELINE_PICK_VALUES: dict[tuple[int, int], float] = {
    (1,1):72.0,(1,2):68.0,(1,3):64.0,(1,4):60.5,(1,5):57.0,(1,6):53.5,(1,7):50.0,
    (1,8):47.0,(1,9):44.0,(1,10):41.5,(1,11):39.0,(1,12):37.0,(1,13):35.0,(1,14):33.0,
    (2,1):30.0,(2,2):28.5,(2,3):27.0,(2,4):25.5,(2,5):24.0,(2,6):22.5,(2,7):21.5,
    (2,8):20.5,(2,9):19.5,(2,10):18.5,(2,11):17.5,(2,12):16.5,(2,13):15.5,(2,14):14.5,
    (3,1):13.0,(3,2):12.0,(3,3):11.0,(3,4):10.5,(3,5):10.0,(3,6):9.5,(3,7):9.0,
    (3,8):8.5,(3,9):8.0,(3,10):7.5,(3,11):7.0,(3,12):6.5,(3,13):6.0,(3,14):5.5,
    (4,1):5.0,(4,2):4.5,(4,3):4.0,(4,4):3.5,(4,5):3.0,(4,6):2.5,(4,7):2.0,
    (4,8):1.8,(4,9):1.6,(4,10):1.4,(4,11):1.2,(4,12):1.0,(4,13):0.8,(4,14):0.6,
}

YEAR_PENALTY: dict[int, float] = {0:1.00, 1:0.85, 2:0.72, 3:0.60}


@dataclass
class TradeAsset:
    asset_type: str          # "player" or "pick"
    label: str
    mfl_id: Optional[str] = None
    pick_year: Optional[int] = None
    pick_round: Optional[int] = None
    pick_slot: Optional[int] = None   # None = mid-round
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


def build_pick_value_table(dp_picks: list = None) -> dict[tuple[int, int], float]:
    """Build (round, slot) → normalized 0-100 value from DP picks data."""
    if not dp_picks:
        logger.info("Using baseline pick value table")
        return dict(BASELINE_PICK_VALUES)

    valid = [(p.pick_round, p.pick_slot, p.ecr_1qb) for p in dp_picks
             if p.ecr_1qb is not None and p.pick_round and p.pick_slot]
    if not valid:
        return dict(BASELINE_PICK_VALUES)

    # ecr_1qb is an ECR rank (lower number = better pick = higher value)
    # Invert: best pick (lowest ecr) → highest value
    ecr_values = [v[2] for v in valid]
    ecr_min, ecr_max = min(ecr_values), max(ecr_values)
    span = (ecr_max - ecr_min) or 1.0

    table = {}
    for rnd, slot, ecr in valid:
        # Invert and scale to 0-80 (leave headroom for year penalties)
        norm = max(0.0, min(80.0, (ecr_max - ecr) / span * 80.0))
        table[(rnd, slot)] = round(norm, 1)

    logger.info("DP pick table: %d slots", len(table))
    return table


def _score_pick(asset: TradeAsset, pick_table: dict, current_year: int) -> float:
    rnd = asset.pick_round or 1
    slot = asset.pick_slot
    year = asset.pick_year or current_year
    year_offset = max(0, min(3, year - current_year))

    if slot is not None:
        base = pick_table.get((rnd, slot), BASELINE_PICK_VALUES.get((rnd, slot), 5.0))
    else:
        # Mid-round estimate
        mid = max(1, min(14, 7))
        base = pick_table.get((rnd, mid), BASELINE_PICK_VALUES.get((rnd, mid), 5.0))
        asset.notes = "mid-round estimate"

    penalty = YEAR_PENALTY.get(year_offset, 0.55)
    value = round(base * penalty, 2)
    if not asset.notes:
        asset.notes = f"+{year_offset}yr ×{penalty}" if year_offset > 0 else "current year"
    return value


def _score_player(asset: TradeAsset, consensus_map: dict) -> tuple[float, Optional[float], Optional[float], int, list[str]]:
    disputed = []
    if not asset.mfl_id:
        return 0.0, None, None, 0, [f"{asset.label}: no MFL ID"]
    agg = consensus_map.get(asset.mfl_id)
    if not agg:
        return 0.0, None, None, 0, [f"{asset.label}: not in consensus map"]

    asset.position = asset.position or agg.position
    asset.nfl_team = asset.nfl_team or agg.nfl_team
    asset.age = asset.age or agg.age
    asset.is_disputed = agg.is_disputed

    if agg.is_disputed:
        disputed.append(
            f"{asset.label}: FC={agg.fc_norm:.0f} vs DP={agg.dp_norm:.0f} "
            f"(Δ{agg.disagreement:.0f}) — {agg.value_signal}"
        )
    return agg.consensus_score, agg.fc_norm, agg.dp_norm, agg.sources, disputed


def evaluate_trade(
    side_a_assets: list[TradeAsset],
    side_b_assets: list[TradeAsset],
    consensus_map: dict,
    pick_table: dict[tuple[int, int], float],
    current_year: int = 2026,
) -> TradeVerdict:
    disputed_flags: list[str] = []

    def _score_side(assets: list[TradeAsset]) -> TradeSide:
        side = TradeSide(assets=assets)
        for asset in assets:
            if asset.asset_type == "player":
                score, fc, dp, srcs, flags = _score_player(asset, consensus_map)
                asset.consensus_score = score
                asset.fc_score = fc
                asset.dp_score = dp
                asset.sources = srcs
                disputed_flags.extend(flags)
                side.player_count += 1
            else:
                score = _score_pick(asset, pick_table, current_year)
                asset.consensus_score = score
                asset.sources = 1
                side.pick_count += 1
            side.total_score += asset.consensus_score
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
    from mfl_ai_gm.adapters.dynastyprocess_client import fetch_dp_values, build_dp_mfl_map, fetch_dp_picks
    from mfl_ai_gm.analysis.value_aggregator import build_consensus_values, build_consensus_mfl_map

    fc_map = build_mfl_value_map(fetch_fc_values())
    dp_map = build_dp_mfl_map(fetch_dp_values())
    cmap = build_consensus_mfl_map(build_consensus_values(fc_map, dp_map))
    pick_table = build_pick_value_table(fetch_dp_picks())

    # Bijan Robinson FOR James Cook + 2026 1.05
    side_a = [TradeAsset(asset_type="player", label="Bijan Robinson", mfl_id="16161")]
    side_b = [
        TradeAsset(asset_type="player", label="James Cook", mfl_id="15715"),
        TradeAsset(asset_type="pick", label="2026 1.05", pick_year=2026, pick_round=1, pick_slot=5),
    ]
    v = evaluate_trade(side_a, side_b, cmap, pick_table)

    print(f"\n{'='*55}")
    print(f"  TRADE CALCULATOR")
    print(f"{'='*55}")
    print(f"\nSide A — {v.side_a.total_score:.1f} pts:")
    for a in v.side_a.assets:
        print(f"  {a.label:<30} {a.consensus_score:.1f}  {a.notes or ''}")
    print(f"\nSide B — {v.side_b.total_score:.1f} pts:")
    for a in v.side_b.assets:
        print(f"  {a.label:<30} {a.consensus_score:.1f}  {a.notes or ''}")
    print(f"\nVerdict: {v.fairness} — {v.winner}")
    print(f"Delta:   {v.delta:+.1f} pts  ({v.advantage_pct:.1f}%)")
    print(f"\n{v.summary}")
    print(f"→ {v.recommendation}")
    if v.disputed_assets:
        print("\nDisputed:")
        for d in v.disputed_assets:
            print(f"  ⚠ {d}")
