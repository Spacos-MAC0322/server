"""Combined place search (#59): text, interest groups, zones and price in MongoDB.

build_search_conditions turns the query contract into MongoDB conditions, so the
database does the filtering (ASR03); the repository adds the map area. Results
feed both the map pins and the cards, as one list.
"""

from collections.abc import Sequence

from pymongo.asynchronous.database import AsyncDatabase

from app.core.text import fold, literal_pattern
from app.repositories.place_repository import PlaceRepository
from app.schemas.place import PlaceCardSchema
from app.schemas.search import (
    InterestGroupCountSchema,
    SearchFilterOptionsSchema,
    SearchFilterParams,
)
from app.services.interest_groups import (
    DESCRIPTION_FIELD,
    GROUPS_BY_ID,
    INTEREST_GROUPS,
    MATCH_FIELDS,
    NAME_FIELDS,
    InterestGroup,
)
from app.services.place import summarize_places

# Fields a free-text term may match: the place, its bairro, type and region.
TEXT_FIELDS = (
    "name",
    "address.neighborhood",
    "categories",
    "territory.district",
    "territory.subprefecture",
    "region_zone",
)
MAX_TERMS = 8
VISIBLE = (
    {"publication_status": "published"},
    {"data_quality.location_valid": {"$ne": False}},
)


class UnknownInterestGroup(ValueError):
    """A requested category id is not in the interest-group table."""


def _regex(pattern: str) -> dict:
    return {"$regex": pattern, "$options": "i"}


def _any_field(fields: Sequence[str], pattern: str) -> dict:
    return {"$or": [{field: _regex(pattern)} for field in fields]}


def _group_condition(groups: Sequence[InterestGroup]) -> dict:
    """Any selected group: keywords in type or name, or phrases in the description."""
    names = "|".join(group.name_pattern for group in groups)
    options = _any_field(NAME_FIELDS, names)["$or"]
    phrases = "|".join(group.phrase_pattern for group in groups if group.phrases)
    if phrases:
        options.append({DESCRIPTION_FIELD: _regex(phrases)})
    return {"$or": options}


def build_search_conditions(params: SearchFilterParams) -> list[dict]:
    """MongoDB conditions that must all hold; the area is left to the caller.

    Raises UnknownInterestGroup for a category id outside the table.
    """
    conditions: list[dict] = [*VISIBLE]
    if params.bounds is None and params.near is None:
        conditions.append({"location": {"$ne": None}})
    for term in fold(params.q or "").split()[:MAX_TERMS]:
        conditions.append(_any_field(TEXT_FIELDS, literal_pattern(term)))
    if params.categories:
        unknown = sorted(set(params.categories) - GROUPS_BY_ID.keys())
        if unknown:
            raise UnknownInterestGroup(
                f"Grupo de interesse desconhecido: {', '.join(unknown)}"
            )
        groups = [
            GROUPS_BY_ID[group_id] for group_id in dict.fromkeys(params.categories)
        ]
        conditions.append(_group_condition(groups))
    if params.regions:
        conditions.append({"region_zone": {"$in": list(dict.fromkeys(params.regions))}})
    if params.is_free is not None:
        conditions.append({"admission.is_free": params.is_free})
    if params.max_price is not None:
        # Free places fit any budget; unknown prices are left out.
        conditions.append(
            {
                "$or": [
                    {"admission.is_free": True},
                    {"admission.price_min": {"$lte": params.max_price}},
                ]
            }
        )
    return conditions


class SearchService:
    def __init__(self, database: AsyncDatabase):
        self.repository = PlaceRepository(database)

    async def search(self, params: SearchFilterParams) -> list[PlaceCardSchema]:
        """Matching public places: nearest first with a radius, else by name."""
        documents = await self.repository.find_matching(
            build_search_conditions(params), bounds=params.bounds, near=params.near
        )
        if params.near is None:
            documents.sort(key=lambda doc: (fold(str(doc.get("name", ""))), doc["_id"]))
        return await summarize_places(
            self.repository, documents, schema=PlaceCardSchema
        )

    async def filter_options(self) -> SearchFilterOptionsSchema:
        """What the filter panel can offer, counted over public located places.

        Groups come in table order, including empty ones. priced_places counts
        places whose free entry or minimum price is known: while it is zero,
        price filters would only ever return nothing.
        """
        documents = await self.repository.find_matching(
            [*VISIBLE, {"location": {"$ne": None}}],
            projection=dict.fromkeys((*MATCH_FIELDS, "admission"), 1),
        )
        groups = [
            InterestGroupCountSchema(
                id=group.id,
                label=group.label,
                count=sum(group.matches(doc) for doc in documents),
            )
            for group in INTEREST_GROUPS
        ]
        return SearchFilterOptionsSchema(
            groups=groups,
            priced_places=sum(_has_known_price(doc) for doc in documents),
        )


def _has_known_price(document: dict) -> bool:
    admission = document.get("admission")
    if not isinstance(admission, dict):
        return False
    return (
        admission.get("is_free") is not None or admission.get("price_min") is not None
    )
