"""String extraction and tag-reference matching.

InTouch window (``*.win``) and script files are binary, but animation-link
expressions and script source text are stored as readable ASCII or UTF-16
strings. We pull those strings out and match identifiers against the tag
dictionary.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, Iterator, List, Set

# Valid InTouch tagname characters: A-Z a-z 0-9 ! @ - ? # $ % _ \ &
# (first character must not be a digit).
_TAG_CHARS = r"A-Za-z0-9_!@\-?#$%\\&"
TAG_NAME_RE = re.compile(rf"[A-Za-z_!@?#$%\\&][{_TAG_CHARS}]*")
# An identifier optionally followed by .DotField(s).
TOKEN_RE = re.compile(rf"[A-Za-z_!@?#$%\\&][{_TAG_CHARS}]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_SPLIT_RE = re.compile(r"[\-!@?%&\\#]+")
# "Tag = ..." (assignment, not "==") right after a token.
_ASSIGN_RE = re.compile(r"\s*=(?!=)")

_ASCII_RE = re.compile(rb"[\x20-\x7e\t]{3,}")
_UTF16_RE = re.compile(rb"(?:[\x20-\x7e\t]\x00){3,}")

# Window navigation in scripts: Show "Name", ShowTopWindow("Name"), ...
NAV_RE = re.compile(
    r"\b(?:Show|ShowTopWindow|ShowAt|ShowWindow|ShowHome|ShowGraphic)\s*\(?\s*\"([^\"\r\n]{1,64})\"",
    re.IGNORECASE)
# Remote reference AccessName:Item or AccessName:"Item"
REMOTE_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_\-]*):(\"[^\"\r\n]{1,64}\"|[A-Za-z0-9_.\-/]{1,64})")

# Common InTouch dotfields; "X.<field>" where X is not a tag is reported as
# an unresolved tag reference.
DOTFIELDS = {
    "ack", "alarm", "alarmdisabled", "alarmenabled", "alarmcomment", "almcomment",
    "comment", "devtarget", "engunits", "hihilimit", "hihistatus", "hilimit",
    "histatus", "lolimit", "lolostatus", "lostatus",
    "lololimit", "majordevpct", "majordevstatus", "maxeu", "maxraw", "mineu",
    "minraw", "minordevpct", "minordevstatus", "name", "normal", "offmsg", "onmsg",
    "quality", "qualitylimit", "qualitylimitstring", "qualitystatus",
    "qualitystatusstring", "qualitysubstatus", "reference", "referencecomplete",
    "timedate", "timedatestring", "timedatetime", "timehour", "timeminute",
    "timemsec", "timesecond", "timetime", "timetimestring", "value",
    "unack", "alarmdscenabled", "alarmackmodel",
}


def extract_strings(data: bytes, min_len: int = 3) -> Iterator[str]:
    """Yield printable ASCII and UTF-16LE strings found in binary data."""
    for m in _ASCII_RE.finditer(data):
        if len(m.group()) >= min_len:
            yield m.group().decode("ascii", errors="ignore")
    for m in _UTF16_RE.finditer(data):
        s = m.group().decode("utf-16-le", errors="ignore")
        if len(s) >= min_len:
            yield s


class RefMatcher:
    """Matches identifiers in text against a tag dictionary."""

    def __init__(self, tag_keys: Iterable[str], access_keys: Iterable[str] = ()):
        self.tag_keys: Set[str] = set(tag_keys)
        self.access_keys: Set[str] = set(access_keys)

    def scan(self, texts: Iterable[str]):
        """Return (refs, unresolved, remote refs, windows opened, tags written)."""
        refs: Dict[str, int] = {}
        unresolved: Set[str] = set()
        remote: Set[str] = set()
        opens: Set[str] = set()
        writes: Set[str] = set()
        for text in texts:
            for m in NAV_RE.finditer(text):
                opens.add(m.group(1).strip())
            if self.access_keys and ":" in text:
                for m in REMOTE_RE.finditer(text):
                    if m.group(1).lower() in self.access_keys:
                        remote.add(f"{m.group(1)}:{m.group(2).strip(chr(34))}")
            # Quoted literals are scanned too: scripts often pass tag names
            # as strings (e.g. indirect tag assignment).
            for m in TOKEN_RE.finditer(text):
                token = m.group()
                base, _, field = token.partition(".")
                keys = self._resolve(base)
                for key in keys:
                    refs[key] = refs.get(key, 0) + 1
                if len(keys) == 1 and _ASSIGN_RE.match(text, m.end()):
                    writes.add(keys[0])
                if not keys:
                    first_field = field.split(".", 1)[0].lower()
                    if (first_field in DOTFIELDS and not base.startswith("$")
                            and self.tag_keys and len(base) <= 32):
                        unresolved.add(base)
        return refs, unresolved, remote, opens, writes

    def _resolve(self, base: str) -> List[str]:
        key = base.lower()
        if key in self.tag_keys:
            return [key]
        # "Tag1-Tag2" style expressions without spaces.
        if _SPLIT_RE.search(base):
            return [p.lower() for p in _SPLIT_RE.split(base) if p.lower() in self.tag_keys]
        return []
