"""
Layer 2 — Analysis
Age Curve + Contention Window Calculator

Scores each franchise's dynasty trajectory using:
  - Position-weighted age curves (RB ages fastest, QB slowest)
  - Peak years remaining per player
  - Young core size (players under 25)
  - Draft capital owned
  - Current win %
  - Composite roster avg age

Output: 3-tier contention window label + detailed age profile per franchise.

No I/O, no FastAPI. Consumes LeagueSnapshot only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from mfl_ai_gm.domain.models import Franchise, LeagueSnapshot, Player

# ---------------------------------------------------------------------------
# Position age curve parameters
# ---------------------------------------------------------------------------
# Each position has: peak_start, peak_end, cliff_age, weight
# weight = how much this position drives the contention window
# RB ages fastest → highest weight on age scoring
# QB ages slowest → lowest weight, longest useful window

POSITION_CURVES = {
    "RB": {"peak_start": 22, "peak_end": 26, "cliff": 28, "weight": 1.4},
    "WR": {"peak_start": 23, "peak_end": 28, "cliff": 30, "weight": 1.1},
    "TE": {"peak_start": 25, "peak_end": 30, "cliff": 33, "weight": 0.9},
    "QB": {"peak_start": 25, "peak_end": 32, "cliff": 35, "weight": 0.6},
}

# Contention window thresholds (composite score 0–100)
WINDOW_THRESHOLDS = {
    "Contend Now": 62,   # score >= 62 → Contend Now
    "Transition":  40,   # score >= 40 → Transition
    # below 40        → Rebuild
}

# Inputs weights for composite window score
INPUT_WEIGHTS = {
    "peak_years":    0.30,   # peak years remaining (weighted by position)
    "young_core":    0.25,   # players under 25
    "capital":       0.20,   # draft picks owned
    "win_pct":       0.15,   # current win %
    "roster_age":    0.10,   # raw roster avg age (inverted — younger = better)
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PlayerAgeCurve:
    """Age curve assessment for a single player."""
    player_id: str
    name: str
    position: str
    age: Optional[int]
    peak_years_remaining: float     # estimated seasons left in prime
    is_in_peak: bool
    is_past_peak: bool
    curve_score: float              # 0–100, position-adjusted value score
    position_weight: float


@dataclass
class PositionGroupCurve:
    """Age curve summary for a position group."""
    position: str
    players: list[PlayerAgeCurve]
    avg_age: Optional[float]
    avg_peak_years: float
    group_curve_score: float        # 0–100
    peak_count: int                 # players currently in peak window
    young_count: int                # players under 25
    aging_count: int                # players past cliff age
    notes: list[str] = field(default_factory=list)


@dataclass
class ContentionWindow:
    """
    Full contention window assessment for one franchise.
    """
    franchise_id: str
    franchise_name: str

    # Tier
    window: str                     # "Contend Now", "Transition", "Rebuild"
    window_score: float             # 0–100 composite
    years_in_window: int            # estimated seasons at current tier

    # Component scores (each 0–100)
    peak_years_score: float
    young_core_score: float
    capital_score: float
    win_pct_score: float
    roster_age_score: float

    # Age curve by position
    qb_curve: PositionGroupCurve
    rb_curve: PositionGroupCurve
    wr_curve: PositionGroupCurve
    te_curve: PositionGroupCurve

    # Summary stats
    roster_avg_age: Optional[float]
    young_core_count: int           # players under 25
    peak_player_count: int          # players in peak window
    total_future_picks: int

    recommendation: str = ""
    strengths: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, val))


def _avg(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def _peak_years_remaining(age: Optional[int], position: str) -> float:
    """
    Estimate how many seasons a player has left in their positional prime.
    Returns 0 if past peak, fractional if approaching or past cliff.
    """
    if age is None:
        return 1.5   # unknown age — assume some upside (likely young)

    curve = POSITION_CURVES.get(position, POSITION_CURVES["WR"])
    peak_end = curve["peak_end"]
    cliff = curve["cliff"]
    peak_start = curve["peak_start"]

    if age < peak_start:
        # Pre-peak — years until peak + full peak window
        return float(peak_end - peak_start + (peak_start - age) * 0.5)
    elif age <= peak_end:
        # In peak — years remaining in peak
        return float(peak_end - age + 1)
    elif age <= cliff:
        # Post-peak but pre-cliff — declining value
        return float((cliff - age) * 0.4)
    else:
        # Past cliff — minimal dynasty value
        return 0.0


def _player_curve_score(age: Optional[int], position: str) -> float:
    """
    Score a single player's dynasty value based on age and position curve.
    Returns 0–100.
    """
    if age is None:
        return 55.0   # slight discount for unknown age

    curve = POSITION_CURVES.get(position, POSITION_CURVES["WR"])
    peak_start = curve["peak_start"]
    peak_end = curve["peak_end"]
    cliff = curve["cliff"]

    if age < peak_start - 2:
        # Very young — raw upside, below proven
        return 60.0 + (age - (peak_start - 4)) * 3.0
    elif age < peak_start:
        # Approaching peak — high upside
        return 75.0 + (age - peak_start + 2) * 5.0
    elif age <= peak_end:
        # In peak — maximum value, slight bonus for middle of window
        mid = (peak_start + peak_end) / 2
        distance_from_mid = abs(age - mid)
        return _clamp(95.0 - distance_from_mid * 2.0)
    elif age <= cliff:
        # Declining — linear drop from peak_end to cliff
        pct = (age - peak_end) / (cliff - peak_end)
        return _clamp(85.0 - pct * 55.0)
    else:
        # Past cliff — steep falloff
        years_past = age - cliff
        return _clamp(30.0 - years_past * 8.0)


def _assess_player(player: Player) -> PlayerAgeCurve:
    position = player.position
    curve = POSITION_CURVES.get(position, POSITION_CURVES["WR"])

    peak_years = _peak_years_remaining(player.age, position)
    curve_score = _player_curve_score(player.age, position)
    age = player.age

    in_peak = (
        age is not None and
        curve["peak_start"] <= age <= curve["peak_end"]
    )
    past_peak = (
        age is not None and age > curve["peak_end"]
    )

    return PlayerAgeCurve(
        player_id=player.id,
        name=player.name,
        position=position,
        age=age,
        peak_years_remaining=peak_years,
        is_in_peak=in_peak,
        is_past_peak=past_peak,
        curve_score=curve_score,
        position_weight=curve["weight"],
    )


def _assess_group(players: list[Player], position: str) -> PositionGroupCurve:
    """Build a PositionGroupCurve for a set of same-position players."""
    if not players:
        return PositionGroupCurve(
            position=position,
            players=[],
            avg_age=None,
            avg_peak_years=0.0,
            group_curve_score=0.0,
            peak_count=0,
            young_count=0,
            aging_count=0,
            notes=[f"No {position}s rostered"],
        )

    assessed = [_assess_player(p) for p in players]
    curve = POSITION_CURVES.get(position, POSITION_CURVES["WR"])

    ages = [p.age for p in players if p.age is not None]
    avg_age = round(sum(ages) / len(ages), 1) if ages else None

    peak_years_list = [a.peak_years_remaining for a in assessed]
    avg_peak_years = round(sum(peak_years_list) / len(peak_years_list), 1)

    curve_scores = [a.curve_score for a in assessed]
    group_curve_score = _clamp(sum(curve_scores) / len(curve_scores))

    peak_count = sum(1 for a in assessed if a.is_in_peak)
    young_count = sum(1 for p in players if p.age is not None and p.age < 25)
    aging_count = sum(1 for p in players if p.age is not None and p.age > curve["cliff"])

    notes = []
    if peak_count > 0:
        notes.append(f"{peak_count} in peak window")
    if young_count > 0:
        notes.append(f"{young_count} under 25")
    if aging_count > 0:
        notes.append(f"{aging_count} past cliff age ({curve['cliff']})")
    if avg_age:
        notes.append(f"avg age {avg_age}")

    return PositionGroupCurve(
        position=position,
        players=assessed,
        avg_age=avg_age,
        avg_peak_years=avg_peak_years,
        group_curve_score=group_curve_score,
        peak_count=peak_count,
        young_count=young_count,
        aging_count=aging_count,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Component scorers (each returns 0–100)
# ---------------------------------------------------------------------------

def _score_peak_years(
    qb: PositionGroupCurve,
    rb: PositionGroupCurve,
    wr: PositionGroupCurve,
    te: PositionGroupCurve,
) -> float:
    """
    Weighted average of group curve scores, using position weights.
    RB weighted highest (ages fastest), QB lowest.
    """
    groups = [
        (qb, POSITION_CURVES["QB"]["weight"]),
        (rb, POSITION_CURVES["RB"]["weight"]),
        (wr, POSITION_CURVES["WR"]["weight"]),
        (te, POSITION_CURVES["TE"]["weight"]),
    ]
    total_weight = sum(w for _, w in groups)
    weighted_sum = sum(g.group_curve_score * w for g, w in groups)
    return _clamp(weighted_sum / total_weight)


def _score_young_core(players: list[Player], league_avg_young: float) -> float:
    """Score based on number of players under 25 vs league average."""
    young = sum(1 for p in players if p.age is not None and p.age < 25)
    # 5+ young players = excellent; 0 = critical weakness
    base = _clamp(young * 14.0)   # 7 young players = 98 pts
    # Relative bonus
    if young > league_avg_young:
        base = _clamp(base + 8.0)
    elif young < league_avg_young * 0.5:
        base = _clamp(base - 10.0)
    return base


def _score_capital(total_picks: int, league_avg: float, league_max: int) -> float:
    """Score draft capital. 0 picks = 0, above average = bonus."""
    if total_picks == 0:
        return 0.0
    if league_max == 0:
        return 50.0
    base = _clamp((total_picks / league_max) * 80.0)
    if total_picks > league_avg:
        base = _clamp(base + 15.0)
    return base


def _score_win_pct(wins: int, losses: int, ties: int) -> float:
    """Convert win % to 0–100 score. 0-0 record = 50 (neutral, preseason)."""
    games = wins + losses + ties
    if games == 0:
        return 50.0   # preseason neutral
    pct = (wins + ties * 0.5) / games
    return _clamp(pct * 100.0)


def _score_roster_age(roster_avg_age: Optional[float]) -> float:
    """
    Invert roster avg age into a score.
    Younger = higher score. 24 = 100, 30 = ~40, 33+ = ~0.
    """
    if roster_avg_age is None:
        return 50.0
    # Linear: 23 → 100, 32 → 0
    score = _clamp(100.0 - (roster_avg_age - 23.0) * (100.0 / 9.0))
    return score


def _estimate_years_in_window(window: str, score: float, rb_avg_age: Optional[float]) -> int:
    """
    Rough estimate of how many seasons a team will stay in this tier.
    Based on score margin within tier and RB age (leading indicator of decline).
    """
    if window == "Contend Now":
        margin = score - WINDOW_THRESHOLDS["Contend Now"]
        base = max(1, min(5, int(margin / 8) + 2))
        # Aging RB corps shortens window
        if rb_avg_age and rb_avg_age > 27:
            base = max(1, base - 1)
        return base
    elif window == "Transition":
        return 2
    else:  # Rebuild
        margin = WINDOW_THRESHOLDS["Transition"] - score
        return max(2, min(5, int(margin / 8) + 2))


def _build_recommendation(
    window: str,
    franchise_name: str,
    years: int,
    rb_curve: PositionGroupCurve,
    capital_score: float,
    young_core_score: float,
) -> str:
    if window == "Contend Now":
        rec = f"{franchise_name} is in a {years}-year contention window. "
        if capital_score < 30:
            rec += "Trade picks for proven veterans — your window is now. "
        if rb_curve.aging_count > 0:
            rec += "Address aging RB corps before the cliff. "
        rec += "Prioritize win-now moves over future assets."
    elif window == "Transition":
        rec = f"{franchise_name} is in transition — {years} years to clarify direction. "
        if young_core_score > 60:
            rec += "Young core suggests trending toward contention. "
            rec += "Add proven pieces around emerging talent. "
        else:
            rec += "Consider selling veterans at peak value. "
            rec += "Accumulate picks and young players."
    else:
        rec = f"{franchise_name} is in rebuild mode — {years} years to retool. "
        if capital_score > 50:
            rec += "Good pick capital — prioritize youth in upcoming drafts. "
        else:
            rec += "Acquire picks aggressively. "
        rec += "Sell aging veterans, target players under 24."
    return rec


# ---------------------------------------------------------------------------
# Main calculator
# ---------------------------------------------------------------------------

def calculate_contention_window(
    franchise: Franchise,
    snapshot: LeagueSnapshot,
    league_context: dict,
) -> ContentionWindow:
    """
    Calculate contention window for a single franchise.
    Call compute_age_curve_context() first for league_context.
    """
    players = snapshot.get_skill_players(franchise.id)

    # Split by position
    qbs = [p for p in players if p.position == "QB"]
    rbs = [p for p in players if p.position == "RB"]
    wrs = [p for p in players if p.position == "WR"]
    tes = [p for p in players if p.position == "TE"]

    # Age curves per group
    qb_curve = _assess_group(qbs, "QB")
    rb_curve = _assess_group(rbs, "RB")
    wr_curve = _assess_group(wrs, "WR")
    te_curve = _assess_group(tes, "TE")

    # Roster-wide stats
    all_ages = [p.age for p in players if p.age is not None]
    roster_avg_age = round(sum(all_ages) / len(all_ages), 1) if all_ages else None
    young_core_count = sum(1 for p in players if p.age is not None and p.age < 25)
    peak_player_count = sum(
        1 for c in [qb_curve, rb_curve, wr_curve, te_curve]
        for p in c.players if p.is_in_peak
    )
    total_picks = len(franchise.future_picks)

    # Standing
    standing = snapshot.standings.get(franchise.id)
    wins = int(standing.wins) if standing else 0
    losses = int(standing.losses) if standing else 0
    ties = int(standing.ties) if standing else 0

    # Component scores
    peak_years_score = _score_peak_years(qb_curve, rb_curve, wr_curve, te_curve)
    young_core_score = _score_young_core(players, league_context["avg_young_core"])
    capital_score = _score_capital(
        total_picks,
        league_context["avg_picks"],
        league_context["max_picks"],
    )
    win_pct_score = _score_win_pct(wins, losses, ties)
    roster_age_score = _score_roster_age(roster_avg_age)

    # Composite window score
    window_score = _clamp(
        peak_years_score * INPUT_WEIGHTS["peak_years"] +
        young_core_score * INPUT_WEIGHTS["young_core"] +
        capital_score    * INPUT_WEIGHTS["capital"] +
        win_pct_score    * INPUT_WEIGHTS["win_pct"] +
        roster_age_score * INPUT_WEIGHTS["roster_age"]
    )

    # Tier
    if window_score >= WINDOW_THRESHOLDS["Contend Now"]:
        window = "Contend Now"
    elif window_score >= WINDOW_THRESHOLDS["Transition"]:
        window = "Transition"
    else:
        window = "Rebuild"

    years_in_window = _estimate_years_in_window(window, window_score, rb_curve.avg_age)

    # Strengths and concerns
    strengths = []
    concerns = []
    if peak_years_score >= 70:
        strengths.append(f"Strong peak-age core ({peak_years_score:.0f}/100)")
    if young_core_score >= 65:
        strengths.append(f"Large young core — {young_core_count} players under 25")
    if capital_score >= 60:
        strengths.append(f"Good draft capital ({total_picks} picks)")
    if rb_curve.aging_count > 0:
        concerns.append(f"{rb_curve.aging_count} RB(s) past cliff age")
    if qb_curve.group_curve_score < 50:
        concerns.append("QB situation weak for contention")
    if young_core_count == 0:
        concerns.append("No players under 25 — aging roster")
    if capital_score == 0:
        concerns.append("No future picks — limited flexibility")

    recommendation = _build_recommendation(
        window, franchise.name, years_in_window,
        rb_curve, capital_score, young_core_score
    )

    return ContentionWindow(
        franchise_id=franchise.id,
        franchise_name=franchise.name,
        window=window,
        window_score=round(window_score, 1),
        years_in_window=years_in_window,
        peak_years_score=round(peak_years_score, 1),
        young_core_score=round(young_core_score, 1),
        capital_score=round(capital_score, 1),
        win_pct_score=round(win_pct_score, 1),
        roster_age_score=round(roster_age_score, 1),
        qb_curve=qb_curve,
        rb_curve=rb_curve,
        wr_curve=wr_curve,
        te_curve=te_curve,
        roster_avg_age=roster_avg_age,
        young_core_count=young_core_count,
        peak_player_count=peak_player_count,
        total_future_picks=total_picks,
        recommendation=recommendation,
        strengths=strengths,
        concerns=concerns,
    )


def compute_age_curve_context(snapshot: LeagueSnapshot) -> dict:
    """Precompute league-wide averages for relative scoring."""
    young_cores = []
    pick_counts = []

    for f in snapshot.franchises:
        players = snapshot.get_skill_players(f.id)
        young_cores.append(sum(1 for p in players if p.age is not None and p.age < 25))
        pick_counts.append(len(f.future_picks))

    return {
        "avg_young_core": sum(young_cores) / len(young_cores) if young_cores else 3.0,
        "avg_picks": sum(pick_counts) / len(pick_counts) if pick_counts else 5.0,
        "max_picks": max(pick_counts) if pick_counts else 10,
    }


def calculate_all_windows(snapshot: LeagueSnapshot) -> list[ContentionWindow]:
    """Calculate contention windows for all franchises, sorted by window score."""
    context = compute_age_curve_context(snapshot)
    windows = [
        calculate_contention_window(f, snapshot, context)
        for f in snapshot.franchises
    ]
    windows.sort(key=lambda w: w.window_score, reverse=True)
    return windows


# ---------------------------------------------------------------------------
# CLI report
# ---------------------------------------------------------------------------

def print_window_report(windows: list[ContentionWindow]) -> None:
    TIER_ICON = {"Contend Now": "🏆", "Transition": "⚖️ ", "Rebuild": "🔨"}

    print("\n" + "=" * 72)
    print("  DYNASTY CONTENTION WINDOWS — Purple Monkey Dynasty League")
    print("=" * 72)
    print(f"  {'#':<4} {'Franchise':<35} {'Window':<14} {'Score':<7} {'Yrs':<5} {'YoungCore':<10} {'Picks'}")
    print("-" * 72)

    for i, w in enumerate(windows, 1):
        icon = TIER_ICON.get(w.window, "  ")
        print(
            f"  {i:<4} {w.franchise_name:<35} "
            f"{icon} {w.window:<12} {w.window_score:<7.1f} "
            f"{w.years_in_window:<5} {w.young_core_count:<10} {w.total_future_picks}"
        )

    print("=" * 72)

    # Tier groupings
    for tier in ["Contend Now", "Transition", "Rebuild"]:
        tier_teams = [w for w in windows if w.window == tier]
        if not tier_teams:
            continue
        icon = TIER_ICON[tier]
        print(f"\n  {icon} {tier.upper()} ({len(tier_teams)} teams)")
        print("  " + "-" * 68)
        for w in tier_teams:
            print(f"\n  {w.franchise_name} — {w.window_score:.1f}/100")
            print(f"    {w.recommendation}")
            if w.strengths:
                print(f"    ✓ {' | '.join(w.strengths)}")
            if w.concerns:
                print(f"    ⚠ {' | '.join(w.concerns)}")
            print(f"    Age curves → "
                  f"QB:{w.qb_curve.avg_age or '?'} "
                  f"RB:{w.rb_curve.avg_age or '?'} "
                  f"WR:{w.wr_curve.avg_age or '?'} "
                  f"TE:{w.te_curve.avg_age or '?'}")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)

    from mfl_ai_gm.snapshot.builder import load_snapshot
    snapshot = load_snapshot()
    windows = calculate_all_windows(snapshot)
    print_window_report(windows)
