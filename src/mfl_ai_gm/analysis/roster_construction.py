"""
Layer 2 — Analysis
Roster Construction Scorer

Grades each franchise's roster construction on a 0–100 scale across 5 dimensions.
Pure logic — no I/O, no FastAPI, no MFL calls. Consumes LeagueSnapshot only.

Scoring dimensions and weights:
    WR corps         25%  — depth, age spread, upside
    RB corps         22%  — youth, volume carriers, depth
    Draft capital    20%  — future picks quantity and round quality
    QB situation     18%  — starter tier, handcuff/backup
    TE situation     15%  — elite TE premium, depth

Age philosophy: moderate modifier within each positional score.
Production > age, but aging cores are penalized and young cores rewarded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from mfl_ai_gm.domain.models import Franchise, LeagueSnapshot, Player

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Scoring weights — must sum to 1.0
WEIGHTS = {
    "wr": 0.25,
    "rb": 0.22,
    "capital": 0.20,
    "qb": 0.18,
    "te": 0.15,
}

# Age thresholds for moderate age modifier
AGE_PRIME_MIN = 23       # below this: raw/unproven, slight discount
AGE_PRIME_MAX = 27       # 23–27: prime window, no penalty
AGE_DECLINE_START = 29   # 28–30: mild penalty
AGE_DECLINE_HARD = 31    # 31+: hard penalty

# Position sets
QB_POSITIONS = frozenset({"QB"})
RB_POSITIONS = frozenset({"RB"})
WR_POSITIONS = frozenset({"WR"})
TE_POSITIONS = frozenset({"TE"})

# Grades
GRADE_THRESHOLDS = [
    (90, "A+"), (85, "A"), (80, "A-"),
    (75, "B+"), (70, "B"), (65, "B-"),
    (60, "C+"), (55, "C"), (50, "C-"),
    (40, "D"),  (0,  "F"),
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PositionGroupScore:
    """Score breakdown for a single position group."""
    position: str           # "QB", "RB", "WR", "TE", "Capital"
    raw_score: float        # 0–100 before weight
    weighted_score: float   # raw_score * weight
    weight: float
    player_count: int
    avg_age: Optional[float]
    notes: list[str] = field(default_factory=list)

    @property
    def grade(self) -> str:
        return _grade(self.raw_score)


@dataclass
class RosterConstructionScore:
    """Full construction score for one franchise."""
    franchise_id: str
    franchise_name: str
    total_score: float          # 0–100 weighted composite
    grade: str
    rank: int                   # 1 = best in league, set after league-wide scoring
    qb: PositionGroupScore
    rb: PositionGroupScore
    wr: PositionGroupScore
    te: PositionGroupScore
    capital: PositionGroupScore
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _grade(score: float) -> str:
    for threshold, letter in GRADE_THRESHOLDS:
        if score >= threshold:
            return letter
    return "F"


def _age_modifier(age: Optional[int]) -> float:
    """
    Returns a multiplier (0.80–1.05) based on age.
    Moderate philosophy: production > age, but aging cores penalized.
    """
    if age is None:
        return 0.95          # unknown age — slight discount (likely rookie)
    if age < AGE_PRIME_MIN:
        return 0.92          # very young — raw, unproven
    if age <= AGE_PRIME_MAX:
        return 1.05          # prime window — bonus
    if age <= AGE_DECLINE_START:
        return 1.00          # late prime — neutral
    if age <= AGE_DECLINE_HARD:
        return 0.90          # decline zone — mild penalty
    return 0.80              # 31+ — hard penalty


def _group_age_modifier(players: list[Player]) -> float:
    """Average age modifier across a position group."""
    if not players:
        return 1.0
    mods = [_age_modifier(p.age) for p in players]
    return sum(mods) / len(mods)


def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, val))


def _avg_age(players: list[Player]) -> Optional[float]:
    ages = [p.age for p in players if p.age is not None]
    if not ages:
        return None
    return round(sum(ages) / len(ages), 1)


# ---------------------------------------------------------------------------
# Position group scorers
# ---------------------------------------------------------------------------

def _score_qb(qbs: list[Player], league_avg_qb_count: float) -> PositionGroupScore:
    """
    QB scoring:
    - 1 clear starter (40 pts base)
    - Starter age modifier (up to ±8 pts)
    - Backup/handcuff depth (up to 20 pts)
    - Extra depth beyond 2 (up to 10 pts)
    - Penalize if 0 QBs
    """
    notes = []
    score = 0.0

    if not qbs:
        notes.append("No QB on roster — critical gap")
        return PositionGroupScore("QB", 0.0, 0.0, WEIGHTS["qb"], 0, None, notes)

    # Sort by assumed starter = oldest (proxy for experience/proven)
    # In a real system this would use FantasyPros rankings — for now use age
    starter = max(qbs, key=lambda p: p.age or 0)
    backups = [p for p in qbs if p != starter]

    # Starter base
    score += 40.0
    starter_mod = _age_modifier(starter.age)
    age_bonus = (starter_mod - 1.0) * 40   # scale modifier to pts
    score += age_bonus
    notes.append(f"Starter: {starter.name} (age {starter.age or '?'})")

    # Depth
    if len(backups) >= 1:
        score += 20.0
        notes.append(f"Backup depth: {len(backups)} QB(s)")
    else:
        notes.append("No QB backup — vulnerable to injury")

    if len(backups) >= 2:
        score += 10.0

    # Relative to league — having more QBs than average is a mild bonus
    if len(qbs) > league_avg_qb_count:
        score += 5.0

    # Cap remaining at 25 pts for age curve of full group
    group_mod = _group_age_modifier(qbs)
    score = score * group_mod

    score = _clamp(score)
    return PositionGroupScore(
        "QB", score, score * WEIGHTS["qb"], WEIGHTS["qb"],
        len(qbs), _avg_age(qbs), notes
    )


def _score_rb(rbs: list[Player]) -> PositionGroupScore:
    """
    RB scoring — dynasty emphasis on youth and volume carriers.
    - 0 RBs: 0
    - Each RB up to 4: base points
    - Youth bonus (RBs age 22–25 are most valuable in dynasty)
    - Depth beyond 4: diminishing returns
    - Heavy age penalty for RB corps avg > 27 (RBs age faster)
    """
    notes = []
    score = 0.0

    if not rbs:
        notes.append("No RBs on roster — critical gap")
        return PositionGroupScore("RB", 0.0, 0.0, WEIGHTS["rb"], 0, None, notes)

    # Base: up to 4 starters worth of value
    starter_rbs = rbs[:4]
    score += min(len(starter_rbs), 4) * 15.0   # 15 pts per starter-tier RB, max 60

    # Depth beyond 4
    depth_rbs = rbs[4:]
    score += min(len(depth_rbs), 4) * 4.0       # diminishing — 4 pts each, max 16

    # Youth bonus: extra credit for RBs aged 22–25 (dynasty sweet spot)
    young_rbs = [p for p in rbs if p.age is not None and 22 <= p.age <= 25]
    score += min(len(young_rbs), 3) * 5.0
    if young_rbs:
        names = ", ".join(p.name.split(",")[0] for p in young_rbs[:3])
        notes.append(f"Young RBs (22–25): {names}")

    # Age modifier — RBs decline faster, so moderate penalty is appropriate
    rb_avg_age = _avg_age(rbs)
    if rb_avg_age:
        if rb_avg_age > 28:
            score *= 0.85
            notes.append(f"Aging RB corps (avg {rb_avg_age}) — dynasty concern")
        elif rb_avg_age > 26:
            score *= 0.93
        elif rb_avg_age <= 25:
            score *= 1.05
            notes.append(f"Young RB corps (avg {rb_avg_age}) — dynasty asset")

    score = _clamp(score)
    notes.insert(0, f"{len(rbs)} RBs rostered")
    return PositionGroupScore(
        "RB", score, score * WEIGHTS["rb"], WEIGHTS["rb"],
        len(rbs), rb_avg_age, notes
    )


def _score_wr(wrs: list[Player]) -> PositionGroupScore:
    """
    WR scoring — depth and age spread are king.
    - WR is the highest-weighted position
    - Rewards having 6+ WRs (dynasty depth)
    - Rewards age spread (mix of young and prime)
    - Penalizes thin WR rooms (< 4)
    """
    notes = []
    score = 0.0

    if not wrs:
        notes.append("No WRs on roster — critical gap")
        return PositionGroupScore("WR", 0.0, 0.0, WEIGHTS["wr"], 0, None, notes)

    # Base depth score
    # Dynasty rosters carry 6–10 WRs — reward that
    depth_score = min(len(wrs), 10) * 8.0       # up to 80 pts for 10 WRs
    score += depth_score

    if len(wrs) < 4:
        score *= 0.70
        notes.append(f"Thin WR room ({len(wrs)} WRs) — significant weakness")
    elif len(wrs) < 6:
        notes.append(f"Adequate WR depth ({len(wrs)} WRs)")
    else:
        notes.append(f"Strong WR depth ({len(wrs)} WRs)")

    # Age spread bonus — want mix of young upside + prime production
    young = [p for p in wrs if p.age is not None and p.age <= 25]
    prime = [p for p in wrs if p.age is not None and 26 <= p.age <= 28]
    old   = [p for p in wrs if p.age is not None and p.age >= 29]

    if young:
        score += min(len(young), 3) * 3.0
        names = ", ".join(p.name.split(",")[0] for p in young[:3])
        notes.append(f"Young WRs (≤25): {names}")
    if prime:
        score += min(len(prime), 3) * 2.0
    if old and len(old) > len(young) + len(prime):
        score *= 0.90
        notes.append(f"WR corps skewing old ({len(old)} aged 29+)")

    # Group age modifier
    group_mod = _group_age_modifier(wrs)
    score = score * ((group_mod - 1.0) * 0.5 + 1.0)   # dampen modifier for WR

    score = _clamp(score)
    notes.insert(0, f"{len(wrs)} WRs rostered")
    return PositionGroupScore(
        "WR", score, score * WEIGHTS["wr"], WEIGHTS["wr"],
        len(wrs), _avg_age(wrs), notes
    )


def _score_te(tes: list[Player]) -> PositionGroupScore:
    """
    TE scoring — elite TE is a massive advantage in dynasty PPR.
    - Having 1 proven TE: solid base
    - Depth at TE: modest bonus (streaming is acceptable)
    - Age: TEs peak later (25–30), so age curve is different
    """
    notes = []
    score = 0.0

    if not tes:
        notes.append("No TE on roster — must stream weekly")
        return PositionGroupScore("TE", 20.0, 20.0 * WEIGHTS["te"], WEIGHTS["te"], 0, None, notes)

    # Base for having a TE at all
    score += 40.0

    # Depth
    if len(tes) >= 2:
        score += 20.0
        notes.append(f"{len(tes)} TEs rostered — good depth")
    else:
        notes.append("1 TE — streaming backup needed")

    if len(tes) >= 3:
        score += 10.0

    # TE age curve peaks later — 25–30 is prime for TEs
    starter_te = tes[0]
    te_age = starter_te.age
    if te_age:
        if 25 <= te_age <= 30:
            score += 15.0
            notes.append(f"Starter TE in prime window (age {te_age})")
        elif te_age < 25:
            score += 8.0
            notes.append(f"Young TE (age {te_age}) — upside play")
        elif te_age > 32:
            score *= 0.85
            notes.append(f"Aging starter TE (age {te_age}) — monitor")

    score = _clamp(score)
    notes.insert(0, f"Starter: {starter_te.name}")
    return PositionGroupScore(
        "TE", score, score * WEIGHTS["te"], WEIGHTS["te"],
        len(tes), _avg_age(tes), notes
    )


def _score_capital(
    franchise: Franchise,
    league_avg_picks: float,
    league_max_picks: int,
) -> PositionGroupScore:
    """
    Draft capital scoring.
    - Total picks owned vs league average
    - Round quality (1st > 2nd > 3rd+)
    - Early round picks in near years worth more
    - Relative to league (contextual scoring)
    """
    notes = []
    score = 0.0

    picks = franchise.future_picks
    total = len(picks)

    if total == 0:
        notes.append("No future picks — all capital traded away")
        return PositionGroupScore("Capital", 0.0, 0.0, WEIGHTS["capital"], 0, None, notes)

    # Round-weighted pick value
    pick_value = 0.0
    firsts = [p for p in picks if p.round == 1]
    seconds = [p for p in picks if p.round == 2]
    thirds_plus = [p for p in picks if p.round >= 3]

    pick_value += len(firsts) * 15.0
    pick_value += len(seconds) * 8.0
    pick_value += len(thirds_plus) * 3.0

    # Normalize to 0–80 range based on league max
    max_possible = league_max_picks * 15.0   # if everyone had all 1sts
    if max_possible > 0:
        score = (pick_value / max_possible) * 80.0

    # Relative bonus: above-average capital
    if total > league_avg_picks:
        score += 10.0
        notes.append(f"Above-average capital ({total} picks vs avg {league_avg_picks:.1f})")
    elif total < league_avg_picks * 0.6:
        score *= 0.80
        notes.append(f"Below-average capital ({total} picks vs avg {league_avg_picks:.1f})")

    # Composition notes
    if firsts:
        notes.append(f"{len(firsts)} 1st-round pick(s)")
    if seconds:
        notes.append(f"{len(seconds)} 2nd-round pick(s)")

    notes.insert(0, f"{total} total future picks")
    score = _clamp(score)
    return PositionGroupScore(
        "Capital", score, score * WEIGHTS["capital"], WEIGHTS["capital"],
        total, None, notes
    )


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------

def score_roster(
    franchise: Franchise,
    snapshot: LeagueSnapshot,
    league_context: dict,
) -> RosterConstructionScore:
    """
    Score a single franchise's roster construction.

    league_context: precomputed league-wide stats (averages, maxes).
    Call compute_league_context() first.
    """
    players = snapshot.get_skill_players(franchise.id)

    # Split by position
    qbs = [p for p in players if p.position in QB_POSITIONS]
    rbs = [p for p in players if p.position in RB_POSITIONS]
    wrs = [p for p in players if p.position in WR_POSITIONS]
    tes = [p for p in players if p.position in TE_POSITIONS]

    # Score each group
    qb_score = _score_qb(qbs, league_context["avg_qb_count"])
    rb_score = _score_rb(rbs)
    wr_score = _score_wr(wrs)
    te_score = _score_te(tes)
    cap_score = _score_capital(
        franchise,
        league_context["avg_pick_count"],
        league_context["max_pick_count"],
    )

    # Weighted total
    total = (
        qb_score.weighted_score +
        rb_score.weighted_score +
        wr_score.weighted_score +
        te_score.weighted_score +
        cap_score.weighted_score
    )
    total = _clamp(total)

    # Derive strengths and weaknesses
    group_scores = {
        "QB": qb_score.raw_score,
        "RB": rb_score.raw_score,
        "WR": wr_score.raw_score,
        "TE": te_score.raw_score,
        "Draft Capital": cap_score.raw_score,
    }
    sorted_groups = sorted(group_scores.items(), key=lambda x: x[1], reverse=True)
    strengths = [f"{g} ({s:.0f}/100)" for g, s in sorted_groups if s >= 70]
    weaknesses = [f"{g} ({s:.0f}/100)" for g, s in sorted_groups if s < 50]

    summary = _build_summary(franchise.name, total, strengths, weaknesses)

    return RosterConstructionScore(
        franchise_id=franchise.id,
        franchise_name=franchise.name,
        total_score=round(total, 1),
        grade=_grade(total),
        rank=0,  # set after league-wide scoring
        qb=qb_score,
        rb=rb_score,
        wr=wr_score,
        te=te_score,
        capital=cap_score,
        strengths=strengths,
        weaknesses=weaknesses,
        summary=summary,
    )


def _build_summary(name: str, score: float, strengths: list[str], weaknesses: list[str]) -> str:
    parts = [f"{name} scores {score:.1f}/100 ({_grade(score)})."]
    if strengths:
        parts.append(f"Strengths: {', '.join(strengths)}.")
    if weaknesses:
        parts.append(f"Weaknesses: {', '.join(weaknesses)}.")
    if not strengths and not weaknesses:
        parts.append("Balanced roster across all dimensions.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# League-wide scoring
# ---------------------------------------------------------------------------

def compute_league_context(snapshot: LeagueSnapshot) -> dict:
    """
    Precompute league-wide averages needed for relative scoring.
    Call once before scoring all franchises.
    """
    qb_counts = []
    pick_counts = []

    for f in snapshot.franchises:
        players = snapshot.get_skill_players(f.id)
        qbs = [p for p in players if p.position in QB_POSITIONS]
        qb_counts.append(len(qbs))
        pick_counts.append(len(f.future_picks))

    return {
        "avg_qb_count": sum(qb_counts) / len(qb_counts) if qb_counts else 2.0,
        "avg_pick_count": sum(pick_counts) / len(pick_counts) if pick_counts else 5.0,
        "max_pick_count": max(pick_counts) if pick_counts else 10,
    }


def score_all_franchises(snapshot: LeagueSnapshot) -> list[RosterConstructionScore]:
    """
    Score every franchise in the league and assign ranks.
    Returns list sorted by total_score descending (rank 1 = best).
    """
    context = compute_league_context(snapshot)
    scores = [
        score_roster(f, snapshot, context)
        for f in snapshot.franchises
    ]
    scores.sort(key=lambda s: s.total_score, reverse=True)
    for i, s in enumerate(scores):
        s.rank = i + 1
    return scores


# ---------------------------------------------------------------------------
# CLI / quick report
# ---------------------------------------------------------------------------

def print_league_report(scores: list[RosterConstructionScore]) -> None:
    """Print a formatted league-wide construction report."""
    print("\n" + "=" * 70)
    print("  ROSTER CONSTRUCTION SCORES — Purple Monkey Dynasty League")
    print("=" * 70)
    print(f"  {'Rank':<5} {'Franchise':<35} {'Score':<8} {'Grade':<6} {'W':<5} {'R':<5} {'QB':<5} {'TE':<5} {'CAP':<5}")
    print("-" * 70)
    for s in scores:
        print(
            f"  {s.rank:<5} {s.franchise_name:<35} {s.total_score:<8.1f} "
            f"{s.grade:<6} {s.wr.raw_score:<5.0f} {s.rb.raw_score:<5.0f} "
            f"{s.qb.raw_score:<5.0f} {s.te.raw_score:<5.0f} {s.capital.raw_score:<5.0f}"
        )
    print("=" * 70)
    print("  Columns: Score | Grade | WR | RB | QB | TE | Capital (all 0–100)")

    print("\n  DETAILED BREAKDOWN (top 3 + bottom 1):")
    for s in scores[:3] + scores[-1:]:
        print(f"\n  [{s.rank}] {s.franchise_name} — {s.total_score:.1f} ({s.grade})")
        print(f"      {s.summary}")
        for group in [s.wr, s.rb, s.capital, s.qb, s.te]:
            print(f"      {group.position:<10} {group.raw_score:>5.1f}/100  {', '.join(group.notes[:2])}")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s — %(message)s")

    from mfl_ai_gm.snapshot.builder import load_snapshot
    snapshot = load_snapshot()
    scores = score_all_franchises(snapshot)
    print_league_report(scores)
