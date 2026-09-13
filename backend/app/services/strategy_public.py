# SPDX-License-Identifier: AGPL-3.0-only
"""Public reasoning availability follows projection authority, including compatibility."""

from app.domain.strategy_situations import StrategyFrame, capabilities


def sanitize_strategy_frame(frame, projection):
    if frame is None:
        return None
    status = getattr(projection, "status", projection)
    allowed = getattr(projection, "reasoning_allowed", status in {"current", "replay"})
    if allowed and status in {"current", "replay"}:
        return frame.model_copy(deep=True)
    return StrategyFrame(
        session_key=frame.session_key,
        sequence=frame.sequence,
        history_sequence=frame.history_sequence,
        history_event_id=frame.history_event_id,
        analysis_time=frame.analysis_time,
        semantic_identity=frame.semantic_identity,
        projection_status="unavailable",
        capabilities=capabilities(),
        limitations=["projection_not_current"],
    )


def sanitize_strategy_state(state, projection):
    state.strategy_frame = sanitize_strategy_frame(state.strategy_frame, projection)
    if state.strategy_frame is not None and state.strategy_frame.projection_status == "unavailable":
        for battle in state.current_battles:
            battle.strategy_context = None
    return state
