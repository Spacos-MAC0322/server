"""Address search: ranks Nominatim matches and keeps only distinct places."""

from app.core.geo import distance_m
from app.integrations.nominatim import GeocodeCandidate, NominatimClient
from app.schemas.geocoding import GeocodeResultSchema

# Praça da Sé, marco zero da cidade.
CITY_CENTER = (-23.5503, -46.6339)
# Matches closer than this are the same place for a radius search: the MASP and
# the café at its door, or two segments of the same avenue.
SAME_PLACE_M = 1500
MAX_RESULTS = 5


def _center_distance(candidate: GeocodeCandidate) -> float:
    return distance_m(*CITY_CENTER, candidate["lat"], candidate["lng"])


def rank_candidates(candidates: list[GeocodeCandidate]) -> list[GeocodeCandidate]:
    """Most relevant first; ties go to the most central, where cultural spaces are.

    Nominatim returns streets that share a name with the same importance and in
    no stable order, so the tiebreak decides which "Rua dos Pinheiros" comes first.
    """
    ranked = sorted(candidates, key=lambda c: (-c["importance"], _center_distance(c)))
    distinct: list[GeocodeCandidate] = []
    for candidate in ranked:
        if all(
            distance_m(kept["lat"], kept["lng"], candidate["lat"], candidate["lng"])
            >= SAME_PLACE_M
            for kept in distinct
        ):
            distinct.append(candidate)
    return distinct[:MAX_RESULTS]


class GeocodingService:
    def __init__(self, client: NominatimClient):
        self.client = client

    @property
    def enabled(self) -> bool:
        return self.client.settings.nominatim_enabled

    async def search_address(self, query: str) -> list[GeocodeResultSchema]:
        """One result is a clear match; several mean the user must choose."""
        candidates = await self.client.search(query)
        return [
            GeocodeResultSchema(
                label=c["label"],
                neighborhood=c["neighborhood"],
                lat=c["lat"],
                lng=c["lng"],
            )
            for c in rank_candidates(candidates)
        ]
