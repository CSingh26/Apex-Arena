# SPDX-License-Identifier: AGPL-3.0-only
"""Product ranking with explicit evidence contributions, never a probability."""

from app.domain.strategy_situations import BattleContext, BattleProminence
from app.services.strategy_inputs import relative


def prominence(
    *,
    gap,
    duration,
    closing,
    position,
    train_size,
    remaining,
    same_team,
    strategy,
    neutralization,
    running,
):
    if not running or neutralization in {
        "red",
        "safety_car",
        "safety_car_ending",
        "virtual_safety_car",
        "virtual_safety_car_ending",
    }:
        return BattleProminence(basis="not_racing")
    result = BattleProminence(
        interval=30
        if gap is not None and gap <= 1
        else 22
        if gap is not None and gap <= 1.5
        else 15
        if gap is not None and gap <= 2
        else 5
        if gap is not None and gap <= 3
        else 0,
        persistence=15 if duration >= 60 else 10 if duration >= 30 else 5 if duration >= 10 else 0,
        closing=15 if closing else 0,
        lead_position=10
        if position == 1
        else 7
        if position in {2, 3}
        else 4
        if position is not None and 4 <= position <= 10
        else 0,
        train=10 if train_size >= 4 else 5 if train_size == 3 else 0,
        remaining_distance=10
        if remaining is not None and remaining <= 3
        else 5
        if remaining is not None and remaining <= 5
        else 0,
        team_relevance=5 if same_team else 0,
        strategy_relevance=5 if strategy else 0,
        basis="green_timing_pressure" if neutralization == "green" else "timing_pressure",
    )
    result.score = sum(
        getattr(result, key)
        for key in (
            "interval",
            "persistence",
            "closing",
            "lead_position",
            "train",
            "remaining_distance",
            "team_relevance",
            "strategy_relevance",
        )
    )
    return result


def contexts(session, pairs, context, frame):
    now = context.analysis_time
    neutral = context.control.neutralization.value
    racing = context.control.lifecycle.value not in {"finished", "suspended"}
    graph = {}
    valid = {pair: session.index.gap(pair, now) for pair in pairs}
    for (a, b), gap in valid.items():
        if gap and gap[0] <= 1:
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set()).add(a)
    output = {}
    for pair in pairs:
        a, b = pair
        gap = valid[pair]
        refs = list(gap[1]) if gap else []
        ring = session.trends.setdefault(pair, [])
        if not gap or neutral not in {"green", "unknown"} or not racing:
            ring.clear()
        elif not ring or ring[-1].ref.event_id != session.index.fields[b]["interval"].ref.event_id:
            row = session.index.fields[b]["interval"]
            if ring and (row.ref.observed_at - ring[-1].ref.observed_at).total_seconds() > 30:
                ring.clear()
            if not ring or row.ref.observed_at > ring[-1].ref.observed_at:
                ring.append(row)
                del ring[:-20]
        duration = (
            (ring[-1].ref.observed_at - ring[0].ref.observed_at).total_seconds() if ring else 0
        )
        closing = len(ring) >= 3 and ring[0].value - ring[-1].value >= 0.15
        refs.extend(r.ref for r in ring)
        members = {a, b}
        pending = [a, b]
        while pending:
            for neighbor in graph.get(pending.pop(), set()) - members:
                members.add(neighbor)
                pending.append(neighbor)
        same, team_refs = session.index.team(pair)
        refs.extend(team_refs)
        first, second = session.products.get(a), session.products.get(b)
        tyres = [p.tyre for p in (first, second) if p]
        for product in (first, second):
            if product:
                refs.extend(product.tyre_refs)
        pace = relative(first, second, now) if neutral == "green" else None
        if pace:
            refs.extend(first.pace_refs + second.pace_refs)
        drs = session.index.fields.get(b, {}).get("drs")
        wing = None
        if session.index.fresh(drs, now) and drs.value in {0, 1, 2, 3, 8, 10, 12, 14}:
            wing = drs.value in {10, 12, 14}
            refs.append(drs.ref)
        permission = context.control.drs_permission
        if permission.evidence:
            from app.services.strategy_inputs import evidence

            refs.append(
                evidence(permission.evidence, context.session_key, "control", "RACE_CONTROL")
            )
        relevant = any(
            tuple(item.participants) == pair
            and item.status == "active"
            and item.kind
            in {"stint_divergence", "relative_pace", "undercut_condition", "overcut_condition"}
            for item in frame.situations
        )
        score = prominence(
            gap=gap[0] if gap else None,
            duration=duration,
            closing=closing,
            position=session.index.fields[a]["position"].value if gap else None,
            train_size=len(members) if gap else 0,
            remaining=None,
            same_team=same,
            strategy=relevant,
            neutralization=neutral,
            running=racing,
        )
        closure = {r.key: r for r in refs}
        # Context is independently bounded; if a rich pace closure cannot fit, withhold pace.
        if len(closure) > 96:
            pace = None
            closure = {r.key: r for r in refs[:96]}
        output[f"{a}:{b}"] = BattleContext(
            tyres=tyres,
            pace=pace,
            duration_seconds=duration if ring else None,
            closing=closing,
            train_members=sorted(members),
            same_reported_team=same,
            drs_permission=permission.value,
            within_one_second=gap[0] <= 1 if gap else None,
            observed_wing_open=wing,
            prominence=score,
            evidence=closure,
            limitations=[
                "attempt_evidence_unavailable",
                "championship_context_unavailable",
                "missing_authoritative_remaining_distance",
            ],
        )
    session.trends = {pair: ring for pair, ring in session.trends.items() if pair in valid}
    return output
