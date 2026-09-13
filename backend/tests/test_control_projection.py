# SPDX-License-Identifier: AGPL-3.0-only
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.domain.models import NormalizedRaceEvent, RaceEventType


def control_event(
    sequence,
    message,
    *,
    time=None,
    flag=None,
    scope="Track",
    sector=None,
    category="Other",
    kind=RaceEventType.RACE_CONTROL,
):
    from app.services.control_normalization import normalize_control_payload

    payload = {
        "message": message,
        "flag": flag,
        "scope": scope,
        "sector": sector,
        "category": category,
    }
    payload["control"] = normalize_control_payload(payload)
    return NormalizedRaceEvent(
        id=UUID(int=sequence),
        session_key="control-test",
        source="synthetic",
        sequence_number=sequence,
        dedup_key=f"control-{sequence}",
        event_time=datetime(2026, 9, 12, tzinfo=UTC) + timedelta(seconds=time or sequence),
        received_at=datetime(2026, 9, 12, tzinfo=UTC),
        event_type=kind,
        payload=payload,
    )


@pytest.mark.parametrize(
    "message, expected",
    [
        ("SAFETY CAR DEPLOYED", "safety_car"),
        ("SAFETY CAR IN THIS LAP", "safety_car_ending"),
        ("SAFETY CAR ENDED", "green"),
        ("VIRTUAL SAFETY CAR DEPLOYED", "virtual_safety_car"),
        ("VSC ENDING", "virtual_safety_car_ending"),
        ("VSC ENDED", "green"),
    ],
)
def test_deployment_ending_and_ended_are_distinct(message, expected):
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control

    state = apply_control(ControlProjection(session_key="control-test"), control_event(1, message))
    assert state.neutralization.value == expected
    assert state.neutralization.evidence.event_id == UUID(int=1)


def test_penalty_and_local_flags_do_not_erase_global_neutralization():
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control

    state = apply_control(
        ControlProjection(session_key="control-test"), control_event(1, "SAFETY CAR DEPLOYED")
    )
    state = apply_control(state, control_event(2, "CAR 4 PENALTY - OVERTAKING UNDER SAFETY CAR"))
    state = apply_control(
        state,
        control_event(
            3, "DOUBLE YELLOW IN SECTOR 2", flag="DOUBLE YELLOW", scope="Sector", sector=2
        ),
    )
    assert state.neutralization.value == "safety_car"
    assert state.sector_flags["2"].value == "double_yellow"
    state = apply_control(
        state, control_event(4, "GREEN IN SECTOR 2", flag="GREEN", scope="Sector", sector=2)
    )
    assert state.neutralization.value == "safety_car"
    assert state.sector_flags["2"].value == "green"


def test_suspension_and_resume_preserve_session_identity_and_terminal_is_terminal():
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control, lap_control_context

    state = apply_control(
        ControlProjection(session_key="control-test"), control_event(1, "RED FLAG", flag="RED")
    )
    assert state.lifecycle.value == "suspended"
    state = apply_control(state, control_event(2, "SESSION RESUMED", category="SessionStatus"))
    assert state.lifecycle.value == "running"
    assert state.neutralization.value == "unknown"
    assert state.track_flag.value == "unknown"
    assert state.session_key == "control-test"
    start = datetime(2026, 9, 12, tzinfo=UTC)
    assert (
        lap_control_context(state, start + timedelta(seconds=3), start + timedelta(seconds=4))
        == "unknown"
    )

    state = apply_control(state, control_event(3, "SAFETY CAR ENDED"))
    assert state.neutralization.value == "green"
    assert state.track_flag.value == "green"
    assert (
        lap_control_context(state, start + timedelta(seconds=4), start + timedelta(seconds=5))
        == "green"
    )

    state = apply_control(state, control_event(4, "SESSION FINISHED", category="SessionStatus"))
    state = apply_control(state, control_event(5, "SESSION RESUMED", category="SessionStatus"))
    assert state.lifecycle.value == "finished"


@pytest.mark.parametrize("message", ["SESSION STARTED", "SESSION RESUMED"])
def test_running_lifecycle_does_not_invent_green_or_erase_known_safety_car(message):
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control

    unknown = apply_control(
        ControlProjection(session_key="control-test"),
        control_event(1, message, category="SessionStatus"),
    )
    assert unknown.lifecycle.value == "running"
    assert unknown.neutralization.value == "unknown"

    safety_car = apply_control(
        ControlProjection(session_key="control-test"),
        control_event(1, "SAFETY CAR DEPLOYED"),
    )
    safety_car = apply_control(
        safety_car,
        control_event(2, message, category="SessionStatus"),
    )
    assert safety_car.lifecycle.value == "running"
    assert safety_car.neutralization.value == "safety_car"


def test_late_higher_sequence_control_cannot_override_newer_effective_condition():
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control

    state = apply_control(
        ControlProjection(session_key="control-test"), control_event(1, "SAFETY CAR ENDED", time=20)
    )
    state = apply_control(state, control_event(2, "SAFETY CAR DEPLOYED", time=10))
    assert state.neutralization.value == "green"
    assert state.sequence == 2
    assert [row.event_id for row in state.history] == [UUID(int=1), UUID(int=2)]


@pytest.mark.parametrize(
    "arrival",
    [
        ("green-1", "red", "resume", "green-20"),
        ("green-1", "red", "green-20", "resume"),
        ("green-1", "resume", "red", "green-20"),
        ("green-1", "green-20", "resume", "red"),
    ],
    ids=["effective-order", "late-resume", "late-red", "late-red-and-resume"],
)
def test_red_resume_green_history_is_effective_time_stable(arrival):
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control, lap_control_context

    facts = {
        "green-1": ("SAFETY CAR ENDED", 1, None, "Other"),
        "red": ("RED FLAG", 10, "RED", "Other"),
        "resume": ("SESSION RESUMED", 15, None, "SessionStatus"),
        "green-20": ("SAFETY CAR ENDED", 20, None, "Other"),
    }
    state = ControlProjection(session_key="control-test")
    for sequence, key in enumerate(arrival, 1):
        message, second, flag, category = facts[key]
        state = apply_control(
            state,
            control_event(sequence, message, time=second, flag=flag, category=category),
        )

    start = datetime(2026, 9, 12, tzinfo=UTC)
    assert (
        lap_control_context(state, start + timedelta(seconds=16), start + timedelta(seconds=19))
        == "unknown"
    )
    assert state.neutralization.value == "green"
    assert state.track_flag.value == "green"
    assert state.neutralization.evidence.observed_at == start + timedelta(seconds=20)
    assert state.track_flag.evidence.observed_at == start + timedelta(seconds=20)


def test_resume_effect_is_unavailable_before_late_source_is_consumed():
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control, lap_control_context

    state = ControlProjection(session_key="control-test")
    for event in (
        control_event(1, "SAFETY CAR ENDED", time=1),
        control_event(2, "RED FLAG", time=10, flag="RED"),
        control_event(4, "SAFETY CAR ENDED", time=20),
    ):
        state = apply_control(state, event)
    start = datetime(2026, 9, 12, tzinfo=UTC)
    assert (
        lap_control_context(state, start + timedelta(seconds=16), start + timedelta(seconds=19))
        == "neutralized"
    )

    state = apply_control(
        state, control_event(5, "SESSION RESUMED", time=15, category="SessionStatus")
    )
    assert (
        lap_control_context(state, start + timedelta(seconds=16), start + timedelta(seconds=19))
        == "unknown"
    )
    assert state.neutralization.value == "green"
    assert state.track_flag.value == "green"


def filled_green_control(last_sequence=122):
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control

    state = apply_control(
        ControlProjection(session_key="control-test"),
        control_event(1, "SAFETY CAR ENDED", time=1),
    )
    for sequence in range(2, last_sequence + 1):
        state = apply_control(state, control_event(sequence, "DRS ENABLED", time=2))
    return state


@pytest.mark.parametrize(
    "last_sequence, arrival",
    [
        (
            119,
            (("RED FLAG", 10, "RED", "Other"), ("SESSION RESUMED", 15, None, "SessionStatus")),
        ),
        (
            119,
            (("SESSION RESUMED", 15, None, "SessionStatus"), ("RED FLAG", 10, "RED", "Other")),
        ),
        (
            122,
            (("RED FLAG", 10, "RED", "Other"), ("SESSION RESUMED", 15, None, "SessionStatus")),
        ),
        (
            122,
            (("SESSION RESUMED", 15, None, "SessionStatus"), ("RED FLAG", 10, "RED", "Other")),
        ),
    ],
    ids=["at-bound-ordered", "at-bound-late-red", "beyond-bound-ordered", "beyond-bound-late-red"],
)
def test_truncated_red_resume_current_is_effective_time_stable(last_sequence, arrival):
    from app.services.control_state import apply_control

    state = filled_green_control(last_sequence)
    resume_event_id = None
    for sequence, (message, second, flag, category) in enumerate(arrival, last_sequence + 1):
        state = apply_control(
            state,
            control_event(sequence, message, time=second, flag=flag, category=category),
        )
        if message == "SESSION RESUMED":
            resume_event_id = UUID(int=sequence)

    assert state.history_truncated
    assert state.lifecycle.value == "running"
    assert state.neutralization.value == "unknown"
    assert state.track_flag.value == "unknown"
    assert state.neutralization.evidence.event_id == resume_event_id
    assert state.track_flag.evidence.event_id == resume_event_id


@pytest.mark.parametrize(
    "message, expected",
    [
        ("SAFETY CAR DEPLOYED", "safety_car"),
        ("VIRTUAL SAFETY CAR DEPLOYED", "virtual_safety_car"),
    ],
)
def test_truncated_resume_preserves_sc_and_vsc_with_original_evidence(message, expected):
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control

    state = apply_control(ControlProjection(session_key="control-test"), control_event(1, message))
    for sequence in range(2, 123):
        state = apply_control(state, control_event(sequence, "DRS ENABLED", time=2))
    state = apply_control(
        state,
        control_event(123, "SESSION RESUMED", time=15, category="SessionStatus"),
    )

    assert state.history_truncated
    assert state.lifecycle.value == "running"
    assert state.neutralization.value == expected
    assert state.neutralization.evidence.event_id == UUID(int=1)


def test_truncated_late_red_and_resume_cannot_resurrect_after_terminal():
    from app.services.control_state import apply_control

    state = filled_green_control()
    state = apply_control(
        state,
        control_event(123, "SESSION FINISHED", time=20, category="SessionStatus"),
    )
    state = apply_control(
        state,
        control_event(124, "SESSION RESUMED", time=15, category="SessionStatus"),
    )
    state = apply_control(state, control_event(125, "RED FLAG", time=10, flag="RED"))

    assert state.lifecycle.value == "finished"
    assert state.lifecycle.evidence.event_id == UUID(int=123)
    assert state.neutralization.value == "unknown"
    assert state.track_flag.value == "unknown"
    assert state.neutralization.evidence.event_id == UUID(int=124)
    assert state.track_flag.evidence.event_id == UUID(int=124)


def test_unknown_control_and_metadata_do_not_invent_racing_start():
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control

    state = apply_control(
        ControlProjection(session_key="control-test"),
        control_event(1, "SAFETY CAR WILL BE AVAILABLE"),
    )
    assert state.lifecycle.value == "unknown"
    assert state.neutralization.value == "unknown"
    event = control_event(2, "", kind=RaceEventType.SESSION_START)
    event.payload = {"date_start": event.event_time.isoformat()}
    state = apply_control(state, event)
    assert state.lifecycle.value == "unknown"


def test_global_drs_permission_is_independent_of_wing_observation_and_gap():
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control

    state = apply_control(
        ControlProjection(session_key="control-test"), control_event(1, "DRS ENABLED")
    )
    assert state.drs_permission.value == "enabled"
    assert state.neutralization.value == "unknown"
    state = apply_control(state, control_event(2, "DRS DISABLED"))
    assert state.drs_permission.value == "disabled"


def test_snapshot_equal_time_ordering_and_history_bound():
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control

    state = ControlProjection(session_key="control-test")
    for sequence in range(1, 130):
        state = apply_control(state, control_event(sequence, "SAFETY CAR DEPLOYED", time=1))
    state = ControlProjection.model_validate_json(state.model_dump_json())
    state = apply_control(state, control_event(130, "SAFETY CAR ENDED", time=1))
    assert state.neutralization.value == "green"
    assert len(state.history) == 120
    assert state.history_truncated
    assert apply_control(state, control_event(129, "SAFETY CAR DEPLOYED", time=1)) == state


@pytest.mark.parametrize(
    "code, expected",
    [
        (0, "closed"),
        (1, "closed"),
        (8, "unknown"),
        (2, "unknown"),
        (9, "unknown"),
        (10, "open"),
        (12, "open"),
        (14, "open"),
        (None, "unknown"),
        (True, "unknown"),
        (99, "unknown"),
    ],
)
def test_verified_provider_drs_codes_preserve_unknowns(code, expected):
    from app.services.control_normalization import decode_openf1_drs

    assert decode_openf1_drs(code) == expected


def test_lap_crossing_neutralization_is_excluded_even_after_resumption():
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control, lap_control_context

    state = ControlProjection(session_key="control-test")
    for seq, second, message in [
        (1, 1, "SAFETY CAR ENDED"),
        (2, 10, "SAFETY CAR DEPLOYED"),
        (3, 20, "SAFETY CAR ENDED"),
    ]:
        state = apply_control(state, control_event(seq, message, time=second))
    start = datetime(2026, 9, 12, tzinfo=UTC)
    assert (
        lap_control_context(state, start + timedelta(seconds=5), start + timedelta(seconds=25))
        == "neutralized"
    )
    assert (
        lap_control_context(state, start + timedelta(seconds=21), start + timedelta(seconds=30))
        == "green"
    )


def test_missing_pre_lap_control_context_is_unknown_not_green():
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control, lap_control_context

    state = apply_control(
        ControlProjection(session_key="control-test"), control_event(1, "SAFETY CAR ENDED", time=20)
    )
    start = datetime(2026, 9, 12, tzinfo=UTC)
    assert lap_control_context(state, start, start + timedelta(seconds=25)) == "unknown"


def test_sector_yellow_crossing_lap_is_excluded_without_clearing_global_state():
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control, lap_control_context

    state = apply_control(
        ControlProjection(session_key="control-test"), control_event(1, "SAFETY CAR ENDED")
    )
    state = apply_control(
        state, control_event(2, "YELLOW", time=10, scope="Sector", sector=2, flag="YELLOW")
    )
    state = apply_control(
        state, control_event(3, "GREEN", time=20, scope="Sector", sector=2, flag="GREEN")
    )
    start = datetime(2026, 9, 12, tzinfo=UTC)
    assert (
        lap_control_context(state, start + timedelta(seconds=5), start + timedelta(seconds=25))
        == "neutralized"
    )
    assert state.neutralization.value == "green"


@pytest.mark.parametrize("kind", [RaceEventType.SESSION_END, RaceEventType.SESSION_FINISH])
def test_malformed_optional_control_cannot_consume_authoritative_terminal_event(kind):
    from app.domain.control import ControlProjection
    from app.services.control_state import apply_control

    event = control_event(1, "", kind=kind)
    event.payload["control"] = {"sector": 101}
    state = apply_control(ControlProjection(session_key="control-test"), event)
    assert state.sequence == 1
    assert state.lifecycle.value == "finished"
    assert state.lifecycle.evidence.event_id == UUID(int=1)
    assert state.history[-1].semantics.lifecycle == "finished"
    assert state.history[-1].semantics.sector is None
