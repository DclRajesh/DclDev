"""Data model for an analysed InTouch application."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

# Analog alarm-state columns written by DBDump for IOInt/IOReal/Memory* tags.
_ANALOG_ALARM_COLUMNS = (
    "LoLoAlarmState", "LoAlarmState", "HiAlarmState", "HiHiAlarmState",
    "MinorDevAlarmState", "MajorDevAlarmState", "ROCAlarmState",
)


def _attr(attrs: Dict[str, str], key: str) -> str:
    """Case-insensitive attribute lookup."""
    lowered = key.lower()
    for k, v in attrs.items():
        if k.lower() == lowered:
            return v
    return ""


@dataclass
class Tag:
    name: str
    tag_type: str = "Unknown"
    group: str = ""
    comment: str = ""
    access_name: str = ""
    item_name: str = ""
    source: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)
    history: str = ""  # e.g. "Cyclic 10 s", "On change", "Not logged"

    @property
    def key(self) -> str:
        return self.name.lower()

    @property
    def is_io(self) -> bool:
        return self.tag_type.lower().startswith("io")

    @property
    def category(self) -> str:
        t = self.tag_type.lower()
        if t.startswith("io"):
            return "I/O"
        if t.startswith("memory"):
            return "Memory"
        if t.startswith("indirect"):
            return "Indirect"
        return self.tag_type

    @property
    def io_address(self) -> str:
        if not self.access_name:
            return ""
        return f"{self.access_name}:{self.item_name}"

    @property
    def has_alarm(self) -> bool:
        state = _attr(self.attributes, "AlarmState")
        if state and state.strip().lower() not in ("none", "no", ""):
            return True
        return any(_attr(self.attributes, c).strip().lower() == "on"
                   for c in _ANALOG_ALARM_COLUMNS)

    @property
    def is_logged(self) -> bool:
        return bool(self.history) and self.history != "Not logged"

    def get(self, key: str) -> str:
        return _attr(self.attributes, key)


@dataclass
class AccessName:
    name: str
    application: str = ""
    topic: str = ""
    advise_active: str = ""
    protocol: str = ""
    sec_application: str = ""
    sec_topic: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.name.lower()


@dataclass
class Source:
    """A window (mimic) or other file whose contents reference tags."""

    name: str
    path: str
    kind: str  # "window-binary", "window-xml", "script/other"
    size: int = 0
    tag_refs: Counter = field(default_factory=Counter)  # tag key -> count
    unresolved: Set[str] = field(default_factory=set)
    remote_refs: Set[str] = field(default_factory=set)
    opens_windows: Set[str] = field(default_factory=set)

    @property
    def is_window(self) -> bool:
        return self.kind.startswith("window")


@dataclass
class Issue:
    severity: str  # "Error", "Warning", "Info"
    category: str
    item: str
    detail: str


@dataclass
class AnalysisResult:
    app_path: str
    tags: Dict[str, Tag] = field(default_factory=dict)
    access_names: Dict[str, AccessName] = field(default_factory=dict)
    sources: List[Source] = field(default_factory=list)
    tag_db_source: str = ""
    history_source: str = ""
    warnings: List[str] = field(default_factory=list)

    # ---- convenience views -------------------------------------------------
    @property
    def windows(self) -> List[Source]:
        return [s for s in self.sources if s.is_window]

    @property
    def other_sources(self) -> List[Source]:
        return [s for s in self.sources if not s.is_window]

    @property
    def io_tags(self) -> List[Tag]:
        return [t for t in self.tags.values() if t.is_io]

    def tag_usage(self) -> Dict[str, List[Source]]:
        """tag key -> sources that reference it."""
        usage: Dict[str, List[Source]] = defaultdict(list)
        for src in self.sources:
            for key in src.tag_refs:
                usage[key].append(src)
        return usage

    def window_usage(self) -> Dict[str, List[str]]:
        """tag key -> names of windows that reference it."""
        return {k: [s.name for s in v if s.is_window]
                for k, v in self.tag_usage().items()}

    def unused_tags(self) -> List[Tag]:
        usage = self.tag_usage()
        return [t for t in self.tags.values() if t.key not in usage]

    def tags_per_access_name(self) -> Counter:
        return Counter(t.access_name.lower() for t in self.io_tags if t.access_name)

    def duplicate_io_addresses(self) -> List[Tuple[str, List[Tag]]]:
        by_addr: Dict[str, List[Tag]] = defaultdict(list)
        for t in self.io_tags:
            if t.access_name and t.item_name:
                by_addr[f"{t.access_name}:{t.item_name}".lower()].append(t)
        return sorted(((v[0].io_address, v) for v in by_addr.values() if len(v) > 1),
                      key=lambda x: x[0].lower())

    def history_counts(self) -> Dict[str, Counter]:
        """history description -> Counter of tag categories (I/O, Memory...)."""
        out: Dict[str, Counter] = defaultdict(Counter)
        for t in self.tags.values():
            if t.is_logged:
                out[t.history][t.category] += 1
        return dict(out)

    def type_counts(self) -> Counter:
        return Counter(t.tag_type for t in self.tags.values())

    def issues(self) -> List[Issue]:
        out: List[Issue] = []
        for w in self.warnings:
            out.append(Issue("Warning", "Analysis", "", w))
        for t in sorted(self.io_tags, key=lambda t: t.key):
            if not t.access_name:
                out.append(Issue("Error", "I/O tag without access name", t.name, t.tag_type))
            elif self.access_names and t.access_name.lower() not in self.access_names:
                out.append(Issue("Error", "Undefined access name", t.name,
                                 f"Access name '{t.access_name}' is not defined"))
            if t.access_name and not t.item_name:
                out.append(Issue("Warning", "I/O tag without item name", t.name,
                                 t.access_name))
        for addr, dup in self.duplicate_io_addresses():
            out.append(Issue("Warning", "Duplicate I/O address", addr,
                             ", ".join(t.name for t in dup)))
        per_access = self.tags_per_access_name()
        for acc in sorted(self.access_names.values(), key=lambda a: a.key):
            if per_access.get(acc.key, 0) == 0:
                out.append(Issue("Info", "Access name has no tags", acc.name,
                                 f"{acc.application}|{acc.topic}"))
        for src in self.sources:
            for ref in sorted(src.unresolved):
                out.append(Issue("Warning", "Reference to undefined tag", ref,
                                 f"{src.kind}: {src.name}"))
        for w in self.windows:
            if not w.tag_refs:
                out.append(Issue("Info", "Window references no tags", w.name,
                                 "No tag names found in this window file"))
        if self.tags:
            unused = self.unused_tags()
            if unused:
                out.append(Issue("Info", "Unused tags",
                                 f"{len(unused)} tags",
                                 "See the Tags tab (Used = No) for the list"))
        return out

    def find_tag(self, name: str) -> Optional[Tag]:
        return self.tags.get(name.lower())
