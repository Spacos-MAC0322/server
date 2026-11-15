"""Public place listings and details with cultural programming."""

import re
from datetime import date, datetime, time, timedelta

from pymongo.asynchronous.database import AsyncDatabase

from app.integrations.mapas_culturais import (
    SAO_PAULO,
    MapasCulturaisClient,
    provider_for_url,
    text,
)
from app.repositories.place_repository import PlaceRepository
from app.schemas.place import (
    EventSchema,
    PlaceDescriptionSchema,
    PlaceDetailSchema,
    PlaceListQuery,
    PlaceSummarySchema,
)


class PlaceService:
    def __init__(self, database: AsyncDatabase):
        self.repository = PlaceRepository(database)

    async def get_place(self, place_id: str) -> PlaceDetailSchema | None:
        document = await self.repository.find_published_by_id(place_id)
        if document is None:
            return None

        curation = await self.repository.find_curation(place_id)
        detail = PlaceDetailSchema.from_mongo(document, curation=curation)
        detail.events = await _fetch_place_events(detail)
        return detail

    async def list_places(
        self,
        query: PlaceListQuery | None = None,
    ) -> list[PlaceSummarySchema]:
        query = query or PlaceListQuery()
        bounds = query.bounds
        if bounds is None:
            documents = await self.repository.find_all()
        else:
            documents = await self.repository.find_in_bounds(*bounds)
        visible = [
            doc
            for doc in documents
            if doc.get("publication_status") == "published"
            and doc.get("location") is not None
            and doc.get("data_quality", {}).get("location_valid") is not False
        ]
        curations = await self.repository.find_curations(doc["_id"] for doc in visible)
        return [
            PlaceSummarySchema.from_mongo(doc, curation=curations.get(doc["_id"]))
            for doc in visible
        ]


async def _fetch_place_events(place: PlaceDetailSchema) -> list[EventSchema]:
    client = MapasCulturaisClient()
    if not client.settings.mapas_culturais_enabled:
        return []
    provider = provider_for_url(str(client.settings.mapas_culturais_base_url))
    external_id = next(
        (
            source.external_id
            for source in place.sources
            if source.provider == provider
            and re.fullmatch(r"[1-9][0-9]*", source.external_id)
        ),
        None,
    )
    if external_id is None:
        return []

    events = await client.fetch_events_by_space(external_id)
    return [
        _event_occurrence(event, occurrence)
        for event in events
        for occurrence in event["occurrences"]
        if occurrence.get("spaceId") is None
        or str(occurrence["spaceId"]) == external_id
    ]


def _event_occurrence(event: dict, occurrence: dict) -> EventSchema:
    rule = occurrence.get("rule")
    rule = rule if isinstance(rule, dict) else {}
    start_date = _occurrence_date(rule.get("startsOn")) or _occurrence_date(
        occurrence.get("startsOn")
    )
    end_date = _occurrence_date(occurrence.get("endsOn"))
    start_time = _occurrence_time(rule.get("startsAt")) or _occurrence_time(
        occurrence.get("startsAt")
    )
    end_time = _occurrence_time(rule.get("endsAt")) or _occurrence_time(
        occurrence.get("endsAt")
    )
    starts_at = _event_datetime(start_date, start_time)
    if end_date is None and start_date is not None and end_time is not None:
        end_date = start_date
        if start_time is not None and end_time < start_time:
            end_date += timedelta(days=1)

    return EventSchema(
        title=event["name"].strip(),
        starts_at=starts_at,
        ends_at=_event_datetime(end_date, end_time),
        schedule_text=text(rule.get("description")),
        price_text=text(rule.get("price")) or text(occurrence.get("price")),
        age_rating=text(event.get("classificacaoEtaria")),
        description=PlaceDescriptionSchema(
            short=text(event.get("shortDescription")),
            long=text(event.get("longDescription")),
        ),
    )


def _occurrence_date(value: object) -> date | None:
    raw = text(value.get("date") if isinstance(value, dict) else value)
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def _occurrence_time(value: object) -> time | None:
    raw = text(value.get("date") if isinstance(value, dict) else value)
    if raw is None:
        return None
    try:
        return time.fromisoformat(raw).replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.fromisoformat(raw).time() if len(raw) > 10 else None
        except ValueError:
            return None


def _event_datetime(day: date | None, clock: time | None) -> datetime | str | None:
    if day is None:
        return None
    if clock is None:
        return day.isoformat()
    return datetime.combine(day, clock, tzinfo=SAO_PAULO)
