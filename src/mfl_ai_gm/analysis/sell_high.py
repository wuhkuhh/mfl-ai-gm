"""
Layer 2 — Analysis
Sell-High Detector

Identifies players on your roster who are at or near peak dynasty trade value
and should be considered for trading before their value declines.

Signals used:
  - FantasyCalc value vs age curve (are they valued higher than age suggests?)
  - 30-day trend (rising fast = sell-high window)
  - Age proximity to positional cliff (aging into decline)
  - Redraft vs dynasty gap (redraft >> dynasty = aging, declining dynasty value)
  - Position rank vs overall rank (positionally scarce = inflated value)

Output per player:
  - sell_score: 0–100 (higher = stronger sell signal)
  - sell_signal: "Strong Sell" / "Consider Selling" / "Hold" / "Buy"
  - reasons: list of signal strings
  - fc_value: current FantasyCalc dynasty value
  - trend_30d: 30-day value change

No I/O, no FastAPI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from mfl_ai_gm.analysis.age_curve import POSITION_CURVES
from mfl_ai_gm.adapters.fantasycalc_client import FCPlayerValue

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Value thresholds for sell signal tier
STRONG_SELL_THRESHOLD = 65
CONSIDER_SELL_THRESHOLD = 45
HOLD_THRESHOLD = 25

# KTC trend thresholds (KTC scale: values move ~10-500 per 30d)
KTC_RISING_FAST = 200
KTC_RISING = 50
KTC_FALLING_FAST = -200
KTC_FALLING = -50

# FC trend thresholds (FC scale)
# 30-day trend thresholds
RISING_FAST = 300      # Strong buy momentum — sell into it
RISING = 100
FALLING_FAST = -300    # Already falling — may be too late
FALLING = -100

# Redraft vs dynasty gap: if redraft value is this much HIGHER than dynasty,
# the player is aging (current production > future value)
REDRAFT_PREMIUM_THRESHOLD = 1500

# Minimum dynasty value to include in sell-high analysis
MIN_VALUE_THRESHOLD = 1000


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SellHighSignal:
    """Sell-high assessment for a single rostered player."""
    mfl_player_id: str
    name: str
    position: str
    nfl_team: str
    age: Optional[float]
    fc_value: int
    overall_rank: int
    position_rank: int
    trend_30d: int
    redraft_value: int
    sell_score: float           # 0–100
    sell_signal: str            # "Strong Sell" / "Consider Selling" / "Hold" / "Buy Low"
    reasons: list[str] = field(default_factory=list)
    tier: Optional[int] = None


@dataclass
class FranchiseSellHighReport:
    """Sell-high report for one franchise."""
    franchise_id: str
    franchise_name: str
    signals: list[SellHighSignal]      # all rostered players with FC values, sorted by sell_score
    strong_sells: list[SellHighSignal]
    consider_sells: list[SellHighSignal]
    buy_lows: list[SellHighSignal]
    summary: str = ""


# ---------------------------------------------------------------------------
# Sell score engine
# ---------------------------------------------------------------------------

def _score_player(
    mfl_id: str,
    name: str,
    fc,
    ktc_trend_30d=None,
    ktc_rank=None,
    ktc_value=None,
) -> SellHighSignal:
    """Compute sell-high score for a single player. KTC trend is primary signal."""
    score = 0.0
    reasons = []

    if fc is None and ktc_value is None:
        return None
    age = fc.age if fc else None
    pos = fc.position if fc else "WR"
    curve = POSITION_CURVES.get(pos, POSITION_CURVES.get("WR", {}))

    # ── Age signals ──────────────────────────────────────────────────────────
    if age is not None and curve:
        cliff = curve.get("cliff", 30)
        peak_end = curve.get("peak_end", 28)
        peak_start = curve.get("peak_start", 24)

        if age >= cliff:
            score += 30
            reasons.append(f"Past positional cliff (age {age:.1f}, cliff={cliff})")
        elif age >= peak_end:
            years_to_cliff = cliff - age
            score += max(0, 25 - (years_to_cliff * 5))
            if years_to_cliff <= 2:
                reasons.append(f"Approaching cliff ({years_to_cliff:.1f} yrs away)")
        elif age >= peak_start:
            # In peak — good sell if trending up (sell into the hype)
            pass  # handled by trend signal below

    # ── Trend signals — KTC primary, FC fallback ─────────────────────────────
    ktc_trend = ktc_trend_30d if ktc_trend_30d is not None else None
    fc_trend = fc.trend_30d if fc else 0

    if ktc_trend is not None:
        # KTC is the community benchmark — weight higher
        if ktc_trend >= KTC_RISING_FAST:
            score += 25
            reasons.append(f"KTC rising fast (+{ktc_trend} in 30d) — sell into momentum")
        elif ktc_trend >= KTC_RISING:
            score += 12
            reasons.append(f"KTC trending up (+{ktc_trend} in 30d)")
        elif ktc_trend <= KTC_FALLING_FAST:
            score -= 12
            reasons.append(f"KTC already falling ({ktc_trend} in 30d) — may be too late")
        elif ktc_trend <= KTC_FALLING:
            score -= 6
            reasons.append(f"KTC softening ({ktc_trend} in 30d)")
    elif fc:
        if fc_trend >= RISING_FAST:
            score += 20
            reasons.append(f"FC rising fast (+{fc_trend} in 30d) — sell into momentum")
        elif fc_trend >= RISING:
            score += 10
            reasons.append(f"FC trending up (+{fc_trend} in 30d)")
        elif fc_trend <= FALLING_FAST:
            score -= 10
            reasons.append(f"FC already falling ({fc_trend} in 30d)")
        elif fc_trend <= FALLING:
            score -= 5

    # ── Redraft vs dynasty gap (FC only) ─────────────────────────────────────
    if fc:
        redraft_premium = fc.redraft_value - fc.value
        if redraft_premium >= REDRAFT_PREMIUM_THRESHOLD:
            score += 20
            reasons.append(
                f"Redraft (${fc.redraft_value}) >> dynasty (${fc.value}) — aging producer"
            )
        elif redraft_premium >= REDRAFT_PREMIUM_THRESHOLD // 2:
            score += 10
            reasons.append("Redraft outpacing dynasty value — watch for decline")

    # ── High overall value — use KTC rank if available ────────────────────────
    rank = ktc_rank if ktc_rank is not None else (fc.overall_rank if fc else 999)
    if rank <= 10:
        score += 15
        reasons.append(f"Top-10 KTC rank — maximum trade return window")
    elif rank <= 25:
        score += 8
        reasons.append(f"Top-25 KTC rank — strong trade return available")

    # ── Position rank vs overall rank (FC) ───────────────────────────────────
    if fc and fc.position_rank <= 3 and fc.overall_rank <= 20:
        score += 10
        reasons.append(f"#{fc.position_rank} at {pos} — positional premium")

    # ── Clamp score ──────────────────────────────────────────────────────────
    score = max(-10.0, min(100.0, score))

    # ── Signal tier ──────────────────────────────────────────────────────────
    if score >= STRONG_SELL_THRESHOLD:
        signal = "Strong Sell"
    elif score >= CONSIDER_SELL_THRESHOLD:
        signal = "Consider Selling"
    elif score >= HOLD_THRESHOLD:
        signal = "Hold"
    else:
        signal = "Buy Low"

    trend_display = ktc_trend_30d if ktc_trend_30d is not None else (fc.trend_30d if fc else 0)
    return SellHighSignal(
        mfl_player_id=mfl_id,
        name=name,
        position=pos,
        nfl_team=fc.nfl_team if fc else "FA",
        age=age,
        fc_value=ktc_value if ktc_value is not None else (fc.value if fc else 0),
        overall_rank=ktc_rank if ktc_rank is not None else (fc.overall_rank if fc else 999),
        position_rank=fc.position_rank if fc else 999,
        trend_30d=trend_display,
        redraft_value=fc.redraft_value if fc else 0,
        sell_score=round(score, 1),
        sell_signal=signal,
        reasons=reasons,
        tier=fc.tier if fc else None,
    )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def build_franchise_sell_report(
    franchise_id: str,
    franchise_name: str,
    roster_player_ids: list[str],
    mfl_value_map: dict[str, FCPlayerValue],
    snapshot_player_names: dict[str, str],
    ktc_value_map: dict = None,  # mfl_id -> KTCPlayerValue
) -> FranchiseSellHighReport:
    """
    Build sell-high report for one franchise.

    Args:
        franchise_id: MFL franchise ID
        franchise_name: Display name
        roster_player_ids: List of MFL player IDs on this roster
        mfl_value_map: Dict of mfl_id → FCPlayerValue (from build_mfl_value_map)
        snapshot_player_names: Dict of mfl_id → player name from snapshot
    """
    signals = []

    for pid in roster_player_ids:
        fc = mfl_value_map.get(pid)
        ktc_check = (ktc_value_map or {}).get(pid)
        if fc is None and ktc_check is None:
            continue
        if fc and fc.value < MIN_VALUE_THRESHOLD and (ktc_check is None or ktc_check.value < 500):
            continue
        pos = fc.position if fc else (ktc_check.position if ktc_check else None)
        if pos not in ("QB", "RB", "WR", "TE"):
            continue

        name = snapshot_player_names.get(pid) or (fc.name if fc else pid)
        ktc = (ktc_value_map or {}).get(pid)
        signal = _score_player(
            pid, name, fc,
            ktc_trend_30d=ktc.trend_overall if ktc else None,
            ktc_rank=ktc.rank if ktc else None,
            ktc_value=ktc.value if ktc else None,
        )
        signals.append(signal)

    # Sort by sell_score descending
    signals.sort(key=lambda s: s.sell_score, reverse=True)

    strong_sells = [s for s in signals if s.sell_signal == "Strong Sell"]
    consider_sells = [s for s in signals if s.sell_signal == "Consider Selling"]
    buy_lows = [s for s in signals if s.sell_signal == "Buy Low"]

    # Summary
    if strong_sells:
        top = strong_sells[0]
        summary = (
            f"{franchise_name} has {len(strong_sells)} strong sell candidate(s). "
            f"Top: {top.name} ({top.position}, value {top.fc_value}, score {top.sell_score:.0f}). "
            f"{top.reasons[0] if top.reasons else ''}"
        )
    elif consider_sells:
        top = consider_sells[0]
        summary = (
            f"{franchise_name} — {len(consider_sells)} player(s) worth monitoring for a trade. "
            f"Top: {top.name} ({top.position}, value {top.fc_value})."
        )
    else:
        summary = f"{franchise_name} — no strong sell signals. Roster is in good dynasty shape."

    return FranchiseSellHighReport(
        franchise_id=franchise_id,
        franchise_name=franchise_name,
        signals=signals,
        strong_sells=strong_sells,
        consider_sells=consider_sells,
        buy_lows=buy_lows,
        summary=summary,
    )


def build_all_sell_reports(
    snapshot,
    mfl_value_map: dict[str, FCPlayerValue],
    ktc_value_map: dict = None,
) -> list[FranchiseSellHighReport]:
    """Build sell-high reports for all franchises."""
    # Build name lookup from snapshot
    player_names = {pid: p.name for pid, p in snapshot.players.items()}

    reports = []
    for franchise in snapshot.franchises:
        roster = snapshot.rosters.get(franchise.id)
        if not roster:
            continue
        report = build_franchise_sell_report(
            franchise_id=franchise.id,
            franchise_name=franchise.name,
            roster_player_ids=roster.all_ids,
            mfl_value_map=mfl_value_map,
            snapshot_player_names=player_names,
            ktc_value_map=ktc_value_map,
        )
        reports.append(report)

    return reports


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.path.insert(0, "src")

    from mfl_ai_gm.snapshot.builder import load_snapshot
    from mfl_ai_gm.adapters.fantasycalc_client import fetch_fc_values, build_mfl_value_map

    snapshot = load_snapshot()
    fc_players = fetch_fc_values()
    value_map = build_mfl_value_map(fc_players)

    print(f"\nFC values loaded: {len(value_map)} players with MFL IDs")

    reports = build_all_sell_reports(snapshot, value_map)

    for report in reports:
        print(f"\n{'='*65}")
        print(f"  {report.franchise_name.upper()}")
        print(f"  {report.summary}")
        print("-"*65)

        if not report.signals:
            print("  No players with FC values found.")
            continue

        print(f"  {'Player':<26} {'Pos':<5} {'Age':<6} {'Value':<7} {'Trend':<8} {'Score':<7} Signal")
        print(f"  {'-'*26} {'-'*5} {'-'*6} {'-'*7} {'-'*8} {'-'*7} ------")
        for s in report.signals[:10]:
            age_str = f"{s.age:.1f}" if s.age else "?"
            trend_str = f"{s.trend_30d:+d}"
            print(
                f"  {s.name:<26} {s.position:<5} {age_str:<6} {s.fc_value:<7} "
                f"{trend_str:<8} {s.sell_score:<7.0f} {s.sell_signal}"
            )
            if s.reasons:
                print(f"    → {s.reasons[0]}")
