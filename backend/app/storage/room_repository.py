# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.domain.rooms import (
    AgentProfile,
    MessageEvidence,
    MessageTopic,
    MessageType,
    RaceRoom,
    RoomMessage,
    RoomMode,
    RoomPlaybackState,
    RoomStatus,
)
from app.storage.database import Database
from app.storage.models import (
    AgentProfileRecord,
    MessageEvidenceRecord,
    NormalizedRaceEventRecord,
    RaceRoomAgentRecord,
    RaceRoomRecord,
    RoomMessageRecord,
    RoomPlaybackStateRecord,
)


class SqlRaceRoomRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def seed_agents(self, agents: list[AgentProfile]) -> None:
        async with self.database.session_factory() as session:
            for agent in agents:
                values = agent.model_dump()
                values["supported_topics"] = [topic.value for topic in agent.supported_topics]
                await session.execute(
                    insert(AgentProfileRecord)
                    .values(**values)
                    .on_conflict_do_update(index_elements=["id"], set_=values)
                )
            await session.commit()

    async def upsert_room(self, room: RaceRoom, agent_ids: list[str]) -> RaceRoom:
        values = room.model_dump(exclude={"created_at", "updated_at"})
        values["status"] = room.status.value
        values["mode"] = room.mode.value
        values["source_availability"] = room.source_availability.value
        values["agent_count"] = len(agent_ids)
        dynamic_fields = {
            "id",
            "message_count",
            "current_lap",
            "last_event_at",
            "created_at",
            "updated_at",
        }
        update_values = {key: value for key, value in values.items() if key not in dynamic_fields}
        update_values["session_key"] = func.coalesce(
            values.get("session_key"), RaceRoomRecord.session_key
        )
        for field in ("status", "mode", "source_availability", "telemetry_quality"):
            update_values[field] = case(
                (RaceRoomRecord.message_count > 0, getattr(RaceRoomRecord, field)),
                else_=values[field],
            )
        async with self.database.session_factory() as session:
            await session.execute(
                insert(RaceRoomRecord)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["slug"],
                    set_=update_values,
                )
            )
            record = (
                await session.execute(
                    select(RaceRoomRecord).where(RaceRoomRecord.slug == room.slug)
                )
            ).scalar_one()
            for order, agent_id in enumerate(agent_ids, start=1):
                await session.execute(
                    insert(RaceRoomAgentRecord)
                    .values(
                        room_id=record.id,
                        agent_id=agent_id,
                        is_active=True,
                        sort_order=order * 10,
                    )
                    .on_conflict_do_nothing(constraint="uq_room_agent")
                )
            await session.commit()
            await session.refresh(record)
            return RaceRoom.model_validate(record, from_attributes=True)

    async def list_rooms(
        self,
        *,
        season: int | None = None,
        status: RoomStatus | None = None,
        mode: RoomMode | None = None,
        search: str | None = None,
        sort: str = "race_date_desc",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[RaceRoom], int]:
        filters = []
        if season is not None:
            filters.append(RaceRoomRecord.season == season)
        if status is not None:
            filters.append(RaceRoomRecord.status == status.value)
        if mode is not None:
            filters.append(RaceRoomRecord.mode == mode.value)
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    RaceRoomRecord.race_name.ilike(pattern),
                    RaceRoomRecord.circuit_name.ilike(pattern),
                    RaceRoomRecord.country.ilike(pattern),
                )
            )
        ordering = {
            "race_date_asc": RaceRoomRecord.scheduled_start.asc(),
            "latest_activity": RaceRoomRecord.updated_at.desc(),
        }.get(sort, RaceRoomRecord.scheduled_start.desc())
        statement = select(RaceRoomRecord).where(*filters)
        statement = statement.order_by(RaceRoomRecord.is_featured.desc(), ordering)
        statement = statement.offset(offset).limit(limit)
        count_statement = select(func.count(RaceRoomRecord.id)).where(*filters)
        async with self.database.session_factory() as session:
            records = (await session.execute(statement)).scalars().all()
            total = int((await session.execute(count_statement)).scalar_one())
            return (
                [RaceRoom.model_validate(record, from_attributes=True) for record in records],
                total,
            )

    async def get_room(self, slug: str) -> RaceRoom | None:
        async with self.database.session_factory() as session:
            record = (
                await session.execute(select(RaceRoomRecord).where(RaceRoomRecord.slug == slug))
            ).scalar_one_or_none()
            return (
                RaceRoom.model_validate(record, from_attributes=True)
                if record is not None
                else None
            )

    async def get_room_by_session(self, session_key: str) -> RaceRoom | None:
        statement = (
            select(RaceRoomRecord)
            .where(RaceRoomRecord.session_key == session_key)
            .order_by(RaceRoomRecord.is_featured.desc())
            .limit(1)
        )
        async with self.database.session_factory() as session:
            record = (await session.execute(statement)).scalar_one_or_none()
            return (
                RaceRoom.model_validate(record, from_attributes=True)
                if record is not None
                else None
            )

    async def get_agents(self, room_id: UUID) -> list[AgentProfile]:
        statement = (
            select(AgentProfileRecord)
            .join(RaceRoomAgentRecord, RaceRoomAgentRecord.agent_id == AgentProfileRecord.id)
            .where(
                RaceRoomAgentRecord.room_id == room_id,
                RaceRoomAgentRecord.is_active.is_(True),
                AgentProfileRecord.active.is_(True),
            )
            .order_by(RaceRoomAgentRecord.sort_order)
        )
        async with self.database.session_factory() as session:
            records = (await session.execute(statement)).scalars().all()
            return [AgentProfile.model_validate(record, from_attributes=True) for record in records]

    async def next_message_sequence(self, room_id: UUID) -> int:
        statement = select(func.max(RoomMessageRecord.sequence)).where(
            RoomMessageRecord.room_id == room_id
        )
        async with self.database.session_factory() as session:
            return int((await session.execute(statement)).scalar_one_or_none() or 0) + 1

    async def insert_message(
        self, message: RoomMessage, evidence: list[MessageEvidence]
    ) -> tuple[RoomMessage, bool]:
        async with self.database.session_factory() as session:
            await session.execute(
                select(RaceRoomRecord.id)
                .where(RaceRoomRecord.id == message.room_id)
                .with_for_update()
            )
            sequence = (
                int(
                    (
                        await session.execute(
                            select(func.max(RoomMessageRecord.sequence)).where(
                                RoomMessageRecord.room_id == message.room_id
                            )
                        )
                    ).scalar_one_or_none()
                    or 0
                )
                + 1
            )
            stored_message = message.model_copy(update={"sequence": sequence})
            values = stored_message.model_dump()
            for key in ("topic", "message_type", "confidence", "evidence_status"):
                values[key] = getattr(stored_message, key).value
            statement = (
                insert(RoomMessageRecord)
                .values(**values)
                .on_conflict_do_nothing(constraint="uq_room_trigger_agent")
                .returning(RoomMessageRecord.id)
            )
            inserted_id = (await session.execute(statement)).scalar_one_or_none()
            if inserted_id is None:
                return message, False
            for item in evidence:
                item_values = item.model_copy(update={"message_id": inserted_id}).model_dump()
                if item_values.get("metric_value") is not None:
                    item_values["metric_value"] = str(item_values["metric_value"])
                session.add(MessageEvidenceRecord(**item_values))
            await session.execute(
                update(RaceRoomRecord)
                .where(RaceRoomRecord.id == message.room_id)
                .values(
                    message_count=RaceRoomRecord.message_count + 1,
                    current_lap=func.coalesce(message.lap_number, RaceRoomRecord.current_lap),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
            return stored_message.model_copy(update={"id": inserted_id}), True

    async def list_messages(
        self,
        room_id: UUID,
        *,
        after_sequence: int = 0,
        agent_id: str | None = None,
        topic: MessageTopic | None = None,
        message_type: MessageType | None = None,
        lap_from: int | None = None,
        lap_to: int | None = None,
        sequence_from: int | None = None,
        sequence_to: int | None = None,
        limit: int = 100,
    ) -> list[RoomMessage]:
        filters = [
            RoomMessageRecord.room_id == room_id,
            RoomMessageRecord.sequence > after_sequence,
        ]
        if agent_id:
            filters.append(RoomMessageRecord.agent_id == agent_id)
        if topic:
            filters.append(RoomMessageRecord.topic == topic.value)
        if message_type:
            filters.append(RoomMessageRecord.message_type == message_type.value)
        if lap_from is not None:
            filters.append(RoomMessageRecord.lap_number >= lap_from)
        if lap_to is not None:
            filters.append(RoomMessageRecord.lap_number <= lap_to)
        if sequence_from is not None:
            filters.append(RoomMessageRecord.sequence >= sequence_from)
        if sequence_to is not None:
            filters.append(RoomMessageRecord.sequence <= sequence_to)
        statement = (
            select(RoomMessageRecord)
            .where(and_(*filters))
            .order_by(RoomMessageRecord.sequence)
            .limit(limit)
        )
        async with self.database.session_factory() as session:
            records = (await session.execute(statement)).scalars().all()
            return [RoomMessage.model_validate(record, from_attributes=True) for record in records]

    async def message_evidence(self, message_id: UUID) -> list[MessageEvidence]:
        statement = (
            select(MessageEvidenceRecord)
            .where(MessageEvidenceRecord.message_id == message_id)
            .order_by(MessageEvidenceRecord.created_at)
        )
        async with self.database.session_factory() as session:
            records = (await session.execute(statement)).scalars().all()
            return [
                MessageEvidence.model_validate(record, from_attributes=True) for record in records
            ]

    async def message_belongs_to_room(self, room_id: UUID, message_id: UUID) -> bool:
        statement = select(func.count(RoomMessageRecord.id)).where(
            RoomMessageRecord.room_id == room_id,
            RoomMessageRecord.id == message_id,
        )
        async with self.database.session_factory() as session:
            return bool((await session.execute(statement)).scalar_one())

    async def get_message(self, room_id: UUID, message_id: UUID) -> RoomMessage | None:
        statement = select(RoomMessageRecord).where(
            RoomMessageRecord.room_id == room_id,
            RoomMessageRecord.id == message_id,
        )
        async with self.database.session_factory() as session:
            record = (await session.execute(statement)).scalar_one_or_none()
            return (
                RoomMessage.model_validate(record, from_attributes=True)
                if record is not None
                else None
            )

    async def get_playback(self, room_id: UUID) -> RoomPlaybackState:
        async with self.database.session_factory() as session:
            record = await session.get(RoomPlaybackStateRecord, room_id)
            if record is None:
                record = RoomPlaybackStateRecord(room_id=room_id)
                session.add(record)
                await session.commit()
                await session.refresh(record)
            return RoomPlaybackState.model_validate(record, from_attributes=True)

    async def update_playback(
        self,
        room_id: UUID,
        *,
        current_event_sequence: int | None = None,
        current_message_sequence: int | None = None,
        current_lap: int | None = None,
        playback_speed: float | None = None,
        is_paused: bool | None = None,
        started_at: datetime | None = None,
    ) -> RoomPlaybackState:
        await self.get_playback(room_id)
        values = {"updated_at": datetime.now(UTC)}
        if current_event_sequence is not None:
            values["current_event_sequence"] = current_event_sequence
        if current_message_sequence is not None:
            values["current_message_sequence"] = current_message_sequence
        if current_lap is not None:
            values["current_lap"] = current_lap
        if playback_speed is not None:
            values["playback_speed"] = playback_speed
        if is_paused is not None:
            values["is_paused"] = is_paused
        if started_at is not None:
            values["started_at"] = started_at
        async with self.database.session_factory() as session:
            await session.execute(
                update(RoomPlaybackStateRecord)
                .where(RoomPlaybackStateRecord.room_id == room_id)
                .values(**values)
            )
            await session.commit()
        return await self.get_playback(room_id)

    async def max_message_sequence(self, room_id: UUID) -> int:
        statement = select(func.max(RoomMessageRecord.sequence)).where(
            RoomMessageRecord.room_id == room_id
        )
        async with self.database.session_factory() as session:
            return int((await session.execute(statement)).scalar_one_or_none() or 0)

    async def max_message_sequence_for_event(
        self,
        room_id: UUID,
        session_key: str,
        event_sequence: int,
    ) -> int:
        statement = (
            select(func.max(RoomMessageRecord.sequence))
            .join(
                NormalizedRaceEventRecord,
                NormalizedRaceEventRecord.id == RoomMessageRecord.trigger_event_id,
            )
            .where(
                RoomMessageRecord.room_id == room_id,
                NormalizedRaceEventRecord.session_key == session_key,
                NormalizedRaceEventRecord.sequence_number <= event_sequence,
            )
        )
        async with self.database.session_factory() as session:
            return int((await session.execute(statement)).scalar_one_or_none() or 0)

    async def update_room_status(
        self,
        room_id: UUID,
        status: RoomStatus,
        *,
        current_lap: int | None = None,
        last_event_at: datetime | None = None,
    ) -> None:
        values: dict[str, object] = {"status": status.value, "updated_at": datetime.now(UTC)}
        if current_lap is not None:
            values["current_lap"] = current_lap
        if last_event_at is not None:
            values["last_event_at"] = last_event_at
        async with self.database.session_factory() as session:
            await session.execute(
                update(RaceRoomRecord).where(RaceRoomRecord.id == room_id).values(**values)
            )
            await session.commit()

    async def reset_discussion(self, room_id: UUID) -> None:
        message_ids = select(RoomMessageRecord.id).where(RoomMessageRecord.room_id == room_id)
        async with self.database.session_factory() as session:
            await session.execute(
                delete(MessageEvidenceRecord).where(
                    MessageEvidenceRecord.message_id.in_(message_ids)
                )
            )
            await session.execute(
                delete(RoomMessageRecord).where(RoomMessageRecord.room_id == room_id)
            )
            await session.execute(
                update(RaceRoomRecord)
                .where(RaceRoomRecord.id == room_id)
                .values(message_count=0, current_lap=None, last_event_at=None)
            )
            await session.commit()

    async def delete_empty_development_room(self, slug: str) -> bool:
        """Retire a superseded fixture without touching rooms that contain discussion."""
        async with self.database.session_factory() as session:
            room_id = (
                await session.execute(
                    select(RaceRoomRecord.id)
                    .where(
                        RaceRoomRecord.slug == slug,
                        RaceRoomRecord.is_development.is_(True),
                        RaceRoomRecord.message_count == 0,
                        ~select(RoomMessageRecord.id)
                        .where(RoomMessageRecord.room_id == RaceRoomRecord.id)
                        .exists(),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if room_id is None:
                return False
            await session.execute(
                delete(RoomPlaybackStateRecord).where(RoomPlaybackStateRecord.room_id == room_id)
            )
            await session.execute(
                delete(RaceRoomAgentRecord).where(RaceRoomAgentRecord.room_id == room_id)
            )
            await session.execute(delete(RaceRoomRecord).where(RaceRoomRecord.id == room_id))
            await session.commit()
            return True
