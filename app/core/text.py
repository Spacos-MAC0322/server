"""Accent- and case-insensitive text matching for Portuguese place data."""

import re
import unicodedata

# Each plain letter also matches its accented forms, in both cases. MongoDB and
# Python both read these classes, so one pattern serves the query and the counts.
_ACCENTS = {
    "a": "aáàâãäAÁÀÂÃÄ",
    "e": "eéèêëEÉÈÊË",
    "i": "iíìîïIÍÌÎÏ",
    "o": "oóòôõöOÓÒÔÕÖ",
    "u": "uúùûüUÚÙÛÜ",
    "c": "cçCÇ",
}


def fold(value: str) -> str:
    """Lower case, no accents, single spaces: "São  Paulo" → "sao paulo"."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(c for c in decomposed if not unicodedata.combining(c)).split()
    )


def accent_tolerant(pattern: str) -> str:
    """Expand plain vowels and "c" of a regex into classes with accented forms.

    `pattern` must contain no escapes or classes of its own; use
    `literal_pattern` for user text.
    """
    return "".join(
        f"[{_ACCENTS[char]}]" if char in _ACCENTS else char for char in pattern
    )


def literal_pattern(text: str) -> str:
    """Regex matching `text` literally, ignoring accents (with case-insensitive
    matching): "sao" and "São" both find "São Miguel"."""
    return accent_tolerant(re.escape(fold(text)))


# Letters (accented ones included) and digits: what may not touch a keyword's edges.
# Explicit, because MongoDB's \b ignores accented letters while Python's does not.
WORD_CHARS = "0-9A-Za-zÀ-ÖØ-öø-ÿ"


def keyword_pattern(keyword: str) -> str:
    """Regex for a whole-word keyword, ignoring accents (with option "i").

    Keywords are lower-case text without accents. A trailing "*" makes it a
    prefix ("bibliotec*" finds "Biblioteca"); "." and "?" work as in regexes,
    and lookarounds may exclude contexts ("escolas?(?! de samba)"). No classes
    or escapes: accent_tolerant expands the plain letters.
    """
    prefix = keyword.endswith("*")
    stem = accent_tolerant(keyword.removesuffix("*"))
    tail = f"[{WORD_CHARS}]*" if prefix else ""
    return f"(?<![{WORD_CHARS}]){stem}{tail}(?![{WORD_CHARS}])"
