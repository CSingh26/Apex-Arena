# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.domain.rooms import (
    AgentProfile,
    MessageEvidence,
    MessageTopic,
    RaceRoom,
    RoomMessage,
    RoomPlaybackState,
    RoomStatus,
)
from app.storage.database import Database
from app.storage.models import (
    AgentProfileRecord,
    MessageEvidenceRecord,
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
                values = agent.model_dump(mode="json")
                values["supported_topics"] = [topic.value for topic in agent.supported_topics]
                await session.execute(
                    insert(AgentProfileRecord)
                    .values(**values)
                    .on_conflict_do_update(index_elements=["id"], set_=values)
                )
            await session.commit()

    async def upsert_room(self, room: RaceRoom, agent_ids: list[str]) -> RaceRoom:
        values = room.model_dump(exclude={"created_at", "updated_at"}, mode="json")
        values["status"] = room.status.value
        values["mode"] = room.mode.value
        values["source_availability"] = room.source_availability.value
        values["agent_count"] = len(agent_ids)
        async with self.database.session_factory() as session:
            await session.execute(
                insert(RaceRoomRecord)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["slug"],
                    set_={key: value for key, value in values.items() if key != "id"},
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
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[RaceRoom], int]:
        filters = []
        if season is not None:
            filters.append(RaceRoomRecord.season == season)
        if status is not None:
            filters.append(RaceRoomRecord.status == status.value)
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    RaceRoomRecord.race_name.ilike(pattern),
                    RaceRoomRecord.circuit_name.ilike(pattern),
                    RaceRoomRecord.country.ilike(pattern),
                )
            )
        statement = (
            select(RaceRoomRecord)
            .where(*filters)
            .order_by(RaceRoomRecord.is_featured.desc(), RaceRoomRecord.scheduled_start.desc())
            .offset(offset)
            .limit(limit)
        )
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

    async def get_agents(self, room_id: UUID) -> list[AgentProfile]:
        statement = (
            select(AgentProfileRecord)
            .join(RaceRoomAgentRecord, RaceRoomAgentRecord.agent_id == AgentProfileRecord.id)
            .where(
                RaceRoomAgentRecord.room_id == room_id,
                RaceRoomAgentRecord.is_active.is_(True),
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
        values = message.model_dump(mode="json")
        for key in ("topic", "message_type", "confidence", "evidence_status"):
            values[key] = getattr(message, key).value
        statement = (
            insert(RoomMessageRecord)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_room_trigger_agent")
            .returning(RoomMessageRecord.id)
        )
        async with self.database.session_factory() as session:
            inserted_id = (await session.execute(statement)).scalar_one_or_none()
            if inserted_id is None:
                return message, False
            for item in evidence:
                item_values = item.model_copy(update={"message_id": inserted_id}).model_dump(
                    mode="json"
                )
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
            return message.model_copy(update={"id": inserted_id}), True

    async def list_messages(
        self,
        room_id: UUID,
        *,
        after_sequence: int = 0,
        agent_id: str | None = None,
        topic: MessageTopic | None = None,
        lap_from: int | None = None,
        lap_to: int | None = None,
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
        if lap_from is not None:
            filters.append(RoomMessageRecord.lap_number >= lap_from)
        if lap_to is not None:
            filters.append(RoomMessageRecord.lap_number <= lap_to)
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
                MessageEvidence.model_validate(record, from_attributes=True)
                for record in records
            ]

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
        current_sequence: int | None = None,
        playback_speed: float | None = None,
        is_paused: bool | None = None,
    ) -> RoomPlaybackState:
        await self.get_playback(room_id)
        values = {"updated_at": datetime.now(UTC)}
        if current_sequence is not None:
            values["current_sequence"] = current_sequence
        if playback_speed is not None:
            values["playback_speed"] = playback_speed
        if is_paused is not None:
            values["is_paused"] = is_paused
        async with self.database.session_factory() as session:
            await session.execute(
                update(RoomPlaybackStateRecord)
                .where(RoomPlaybackStateRecord.room_id == room_id)
                .values(**values)
            )
            await session.commit()
        return await self.get_playback(room_id)
