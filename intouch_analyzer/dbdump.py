"""Parsers for the InTouch tag database.

The authoritative, documented source is a DBDump CSV (created with the
InTouch *DBDump* utility or WindowMaker > Special > DBDump). Each section
starts with a header row whose first cell is ``:<TagType>`` followed by the
column names, e.g.::

    :mode=ask
    :IOAccess,Application,Topic,AdviseActive,DDEProtocol,...
    "PLC1","DASABCIP","PLC1_Topic",No,No,...
    :IODisc,Group,Comment,Logged,...,AccessName,ItemUseTagname,ItemName,...
    "Pump1_Run","$System","Pump 1 running",No,...,"PLC1",No,"N7:0/1",...

As a fallback, tag names can be pulled out of the binary ``tagname.x``
file heuristically (names only, no types/addresses).
"""

from __future__ import annotations

import csv
import io
import os
from typing import List, Tuple

from .binscan import extract_strings, TAG_NAME_RE
from .models import AccessName, Tag, _attr


def read_text(path: str) -> str:
    with open(path, "rb") as fh:
        data = fh.read()
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    # UTF-16 without BOM: lots of NUL bytes in the first chunk.
    head = data[:2000]
    if head and head.count(b"\x00") > len(head) // 4:
        return data.decode("utf-16-le", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def looks_like_dbdump(path: str) -> bool:
    try:
        text = read_text(path)[:20000].lower()
    except OSError:
        return False
    return any(marker in text for marker in
               (":ioaccess", ":iodisc", ":ioint", ":ioreal", ":iomsg",
                ":memorydisc", ":memoryint", ":memoryreal", ":memorymsg"))


def parse_dbdump(path: str) -> Tuple[List[Tag], List[AccessName], List[str]]:
    text = read_text(path)
    tags: List[Tag] = []
    access: List[AccessName] = []
    warnings: List[str] = []
    section = None
    header: List[str] = []
    src = os.path.basename(path)

    for lineno, row in enumerate(csv.reader(io.StringIO(text)), start=1):
        if not row or not any(c.strip() for c in row):
            continue
        first = row[0].strip()
        if first.startswith(":"):
            if first.lower().startswith(":mode"):
                continue
            section = first[1:].strip()
            header = [c.strip() for c in row[1:]]
            continue
        if section is None:
            warnings.append(f"{src} line {lineno}: data before any section header, skipped")
            continue
        attrs = {header[i]: row[i + 1].strip()
                 for i in range(min(len(header), len(row) - 1))}
        if section.lower() == "ioaccess":
            access.append(AccessName(
                name=first,
                application=_attr(attrs, "Application"),
                topic=_attr(attrs, "Topic"),
                advise_active=_attr(attrs, "AdviseActive"),
                protocol=_attr(attrs, "DDEProtocol"),
                sec_application=_attr(attrs, "SecApplication"),
                sec_topic=_attr(attrs, "SecTopic"),
                attributes=attrs,
            ))
            continue
        item = _attr(attrs, "ItemName")
        if not item and _attr(attrs, "ItemUseTagname").lower() == "yes":
            item = first
        tags.append(Tag(
            name=first,
            tag_type=section,
            group=_attr(attrs, "Group"),
            comment=_attr(attrs, "Comment"),
            access_name=_attr(attrs, "AccessName"),
            item_name=item,
            source=src,
            attributes=attrs,
        ))
    return tags, access, warnings


def parse_tagname_x(path: str) -> List[Tag]:
    """Best-effort tag-name extraction from the binary tagname.x file."""
    with open(path, "rb") as fh:
        data = fh.read()
    seen = set()
    tags: List[Tag] = []
    for s in extract_strings(data):
        s = s.strip()
        if not TAG_NAME_RE.fullmatch(s) or len(s) > 32 or s.startswith("$"):
            continue
        if s.lower() in seen:
            continue
        seen.add(s.lower())
        tags.append(Tag(name=s, tag_type="Unknown", source="tagname.x (heuristic)"))
    return tags
