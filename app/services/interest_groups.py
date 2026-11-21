"""Interest groups for the category filter ("O que você quer ver?", story #6).

Places keep the source's own category (Mapas Culturais space types such as
"Biblioteca Pública" or "Ponto de Cultura"). Three in four São Paulo spaces are
"Ponto de Cultura", so the type alone says little. Each group therefore has:

- keywords, searched in the place's type and name ("Escola de Samba Águia de
  Ouro" is Música by name);
- phrases, the only thing searched in the short description. They must say
  what the place offers ("sala de cinema", "aulas de dança"): a bare "filmes"
  or "teatro" there often means a production company, a collection or a list
  of every art, as in "produtora de filmes". Long descriptions are left out.

Both are whole words, written as in app.core.text.keyword_pattern: "*" makes a
prefix, "(a|b)" groups alternatives, lookarounds exclude contexts
("escolas?(?! de samba)"). Entries are separated by "|". A new keyword or group
is a change to this table only (ASR05); a place may belong to several groups.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.core.text import keyword_pattern

# Document fields (MongoDB paths) searched for keywords and for phrases.
NAME_FIELDS = ("categories", "type", "source_category", "name")
DESCRIPTION_FIELD = "description.short"
MATCH_FIELDS = (*NAME_FIELDS, DESCRIPTION_FIELD)


def _entries(words: str) -> list[str]:
    """Split on "|" outside parentheses: "aulas de (teatro|danca)" is one entry."""
    entries, depth, current = [], 0, ""
    for char in words:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == "|" and depth == 0:
            entries.append(current)
            current = ""
        else:
            current += char
    return [entry.strip() for entry in [*entries, current] if entry.strip()]


def _alternatives(words: str) -> str:
    return "|".join(keyword_pattern(entry) for entry in _entries(words))


@dataclass(frozen=True)
class InterestGroup:
    id: str
    label: str
    keywords: str
    phrases: str = ""

    @property
    def name_pattern(self) -> str:
        """Regex for type and name, for MongoDB ($regex, option "i") and Python."""
        return _alternatives(self.keywords)

    @property
    def phrase_pattern(self) -> str:
        """Regex for the short description; empty when the group has no phrases."""
        return _alternatives(self.phrases)

    def matches(self, place: Mapping[str, Any]) -> bool:
        names = _compiled(self.name_pattern)
        if any(isinstance(v, str) and names.search(v) for v in _name_values(place)):
            return True
        short = _short_description(place)
        return bool(
            self.phrases and short and _compiled(self.phrase_pattern).search(short)
        )


INTEREST_GROUPS = (
    InterestGroup(
        "arte",
        "Arte e Cultura",
        "museu*|galeria*|arte|(?<!ceu das )(?<!ceu  das )artes|atelie*|cultur*"
        "|pinacoteca",
        "exposicoes de arte|exposicao de arte|arte contemporanea|artes visuais"
        "|artes plasticas|galeria de arte|obras de arte|acervo artistico",
    ),
    InterestGroup(
        "teatro",
        "Teatro e dança",
        "teatr*|theatro|espetaculo*|circo*|circense*|danca*|dance|ballet|opera"
        "|artes cenicas",
        "pecas de teatro|apresentacoes teatrais|espetaculos teatrais"
        "|espetaculos de (teatro|danca)|sessoes de teatro|teatro de arena"
        "|(aulas|oficinas?|cursos?) de (teatro|danca|ballet)|escolas? de danca"
        "|(grupo|companhia) de teatro|salas? de espetaculo*|artes cenicas"
        "|(atividades|espetaculos) circenses|danca (afro|contemporanea)",
    ),
    InterestGroup(
        "musica",
        "Música",
        "music*|samba*|choro*|orquestra*|coral|bandas?|band|hip.?hop|forro|maracatu"
        "|percussao|violao|cavaquinho|jazz|blues|rap|concerto*|sinfonic*|coreto"
        "|casa de shows?",
        "casa de shows?|shows? (acusticos?|musicais)|apresentacoes musicais"
        "|espetaculos musicais|(aulas|oficinas?|cursos?|escola) de musica"
        "|(formacao|educacao|producao) musical|(gravacao|gravacoes) musica*"
        "|estudio de (gravacao|ensaio)|sua orquestra"
        "|(apresentacao|apresentacoes|concertos?) de orquestras?"
        "|coral|samba(?! de gafieira)|choro|hip.?hop|percussao|maracatu",
    ),
    InterestGroup(
        "cinema",
        "Cinema",
        "cinema*|cine|cineclube*|cinemateca|audiovisual",
        "salas? de cinema|(sessao|sessoes) de cinema|cinema ao ar livre|cineclube*"
        "|(exibicao|exibicoes|projecao|projecoes|mostras?) de filmes"
        "|mostras? de cinema|assistir a filmes|cinema com capacidade",
    ),
    InterestGroup(
        "leitura",
        "Bibliotecas e leitura",
        "bibliotec*|leitura|livros?|livrari*|literatur*|literari*|poesia|poetas?"
        "|sarau*|saraus|gibiteca|sebo",
        "sarau*|saraus|biblioteca comunitaria|(acervo|emprestimo) de livros"
        "|mediacao de leitura|(contacao|contacoes) de historias|clube de leitura"
        "|lancamentos? de livros|eventos literarios|(pontos?|salas?) de leitura"
        "|livraria*",
    ),
    InterestGroup(
        "parques",
        "Parques e Praças",
        # Many bairros are "Parque ..." or "Jardim ...": only a leading "Parque" or
        # an explicit park name counts.
        "^parques?|parque (estadual|ecologico|municipal|do|da|dos)|^bosque*"
        "|jardim botanico|horto florestal|^horto|praca*|zoologico|area verde",
        "parque (ecologico|estadual|municipal|da juventude|do ibirapuera)"
        "|extenso parque|dentro do parque",
    ),
    InterestGroup(
        "patrimonio",
        "Patrimônio e história",
        # Not "arquivo", "Documentação" or "Bem Imóvel": on Mapas those types hold
        # a dance space, a band, a sarau and an events company. Real archives
        # say "Histórico" in the name.
        "patrimonio*|historic*|historia(?! natural)|memoria*|monumento*"
        "|arqueolog*|bens culturais",
        "(considerado|reconhecido como) patrimonio*|patrimonio material|tombad*"
        "|museu historico"
        "|(edificio|predio) historico|valor historico|acervo historico"
        "|relevancia historica|(preservar|resgatar|resguardando) a memoria"
        "|memoria (da cidade|do bairro)",
    ),
    InterestGroup(
        "formacao",
        "Cursos e oficinas",
        "escolas?(?! de samba)|oficinas?|cursos?|formacao|formativ*|conservatorio"
        "|fabricas? de cultura",
        "oficinas?|cursos?|aulas (de|gratuitas|para)|workshops?|capacitac*"
        "|(cursos?|oficinas?|acoes) de formacao"
        "|formacao (artistica|cultural|musical|teatral|continuada|de jovens)",
    ),
    InterestGroup(
        "arte-urbana",
        "Arte urbana",
        "grafit*|graffit*|arte urbana|arte de rua|street art|muralismo",
        "grafit*|graffit*|arte urbana|arte de rua|street art|muralismo",
    ),
    InterestGroup(
        "gastronomia",
        "Gastronomia",
        "gastronom*|restaurante*|culinari*",
        "gastronom*|restaurante*",
    ),
)
GROUPS_BY_ID = {group.id: group for group in INTEREST_GROUPS}


def _name_values(place: Mapping[str, Any]) -> list[object]:
    categories = place.get("categories")
    values = list(categories) if isinstance(categories, list) else []
    return [*values, place.get("type"), place.get("source_category"), place.get("name")]


def _short_description(place: Mapping[str, Any]) -> str | None:
    description = place.get("description")
    short = description.get("short") if isinstance(description, Mapping) else None
    return short if isinstance(short, str) else None


_cache: dict[str, re.Pattern[str]] = {}


def _compiled(pattern: str) -> re.Pattern[str]:
    if pattern not in _cache:
        _cache[pattern] = re.compile(pattern, re.IGNORECASE)
    return _cache[pattern]
