"""Historical logging mode / storage rate of tags.

Sources, in order of precedence:

* A Historian tag export (optional), e.g. a Historian configuration export
  with ``TagName``, ``StorageType`` (Cyclic/Delta/Forced) and ``StorageRate``
  (milliseconds) columns. Section-header rows such as ``:(AnalogTag)TagName,...``
  and plain CSV/tab-delimited files with a header row are both accepted.
* DBDump columns: any ``Storage*/Log*/Hist*`` type or rate column, and
  InTouch's own ``Logged`` + ``LogDeadband`` (InTouch logs on data change).
"""

from __future__ import annotations

import csv
import io
import re
from typing import Dict, Optional, Tuple

from .dbdump import read_text

_TYPE_COL = re.compile(r"^(storage|log|hist)\w*(type|mode|method)$", re.I)
_RATE_COL = re.compile(r"^(storage|log|hist|sample)\w*(rate|interval|period)(ms)?$", re.I)
_DEADBAND_COLS = ("LogDeadband", "ValueDeadband", "StorageDeadband")
_NAME_COLS = ("tagname", "tag", "name")

NOT_LOGGED = "Not logged"


def _find(attrs: Dict[str, str], pattern: re.Pattern) -> Tuple[str, str]:
    for k, v in attrs.items():
        if pattern.match(k.strip()) and str(v).strip():
            return k, str(v).strip()
    return "", ""


def _to_float(v: str) -> Optional[float]:
    try:
        return float(v.replace(",", "."))
    except (ValueError, AttributeError):
        return None


def _parse_duration(value: str, column: str, default_unit: str) -> Optional[float]:
    """Return seconds for '10', '10000' (ms column), '00:00:10', '10s', '1 min'."""
    v = value.strip().lower()
    m = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)", v)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.fullmatch(r"([\d.]+)\s*(ms|msec|s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hours?)?", v)
    if not m:
        return None
    num = _to_float(m.group(1))
    if num is None:
        return None
    unit = m.group(2) or ("ms" if "ms" in column.lower() else default_unit)
    if unit.startswith("ms"):
        return num / 1000.0
    if unit in ("m", "min", "mins") or unit.startswith("minute"):
        return num * 60
    if unit.startswith("h"):
        return num * 3600
    return num


def format_seconds(sec: float) -> str:
    if sec < 1:
        return f"{sec * 1000:g} ms"
    if sec < 60 or sec % 60:
        return f"{sec:g} s"
    if sec < 3600 or sec % 3600:
        return f"{sec / 60:g} min"
    return f"{sec / 3600:g} h"


def describe_history(attrs: Dict[str, str], rate_unit: str = "s") -> str:
    """Return e.g. 'Cyclic 10 s', 'On change', 'On change (deadband 0.5)',
    or '' if the attributes say nothing about history."""
    lower = {k.lower(): v for k, v in attrs.items()}
    type_col, stype = _find(attrs, _TYPE_COL)
    rate_col, rate = _find(attrs, _RATE_COL)
    stype_l = stype.lower()
    seconds = _parse_duration(rate, rate_col, rate_unit) if rate else None

    deadband = ""
    for c in _DEADBAND_COLS:
        d = _to_float(str(lower.get(c.lower(), "")))
        if d:
            deadband = f"{d:g}"
            break

    if stype_l in ("none", "off", "no", "not stored", "notstored", "false"):
        return NOT_LOGGED
    if stype_l.startswith("cyclic") or (not stype_l and seconds):
        return f"Cyclic {format_seconds(seconds)}" if seconds else "Cyclic"
    if stype_l.startswith("forced"):
        return "Forced (every change)"
    on_change = f"On change (deadband {deadband})" if deadband else "On change"
    if stype_l:  # delta / on change / anything else that is stored
        return on_change if stype_l.startswith(("delta", "on", "change", "dead")) else stype
    logged = lower.get("logged", "").strip().lower()
    if logged == "yes":
        return on_change
    if logged == "no":
        return NOT_LOGGED
    return ""


def history_sort_key(desc: str):
    """Order: cyclic (fastest first), forced, on change, others."""
    m = re.match(r"Cyclic\s+(.+)$", desc)
    if m:
        return (0, _parse_duration(m.group(1), "", "s") or 0, desc)
    if desc.startswith("Cyclic"):
        return (0, 0, desc)
    if desc.startswith("Forced"):
        return (1, 0, desc)
    if desc.startswith("On change"):
        return (2, 0, desc)
    return (3, 0, desc)


def parse_history_file(path: str) -> Dict[str, str]:
    """Parse a Historian tag export into {tag key: history description}."""
    text = read_text(path)
    first_line = text.lstrip().splitlines()[0] if text.strip() else ""
    delim = "\t" if first_line.count("\t") > first_line.count(",") else ","
    out: Dict[str, str] = {}
    header = None
    for row in csv.reader(io.StringIO(text), delimiter=delim):
        if not row or not any(c.strip() for c in row):
            continue
        first = row[0].strip()
        if first.startswith(":"):
            if first.lower().startswith(":mode"):
                continue
            # ":(AnalogTag)TagName" -> first column is the tag name
            header = ["TagName"] + [c.strip() for c in row[1:]]
            continue
        if header is None:
            if any(c.strip().lower() in _NAME_COLS for c in row):
                header = [c.strip() for c in row]
            continue
        attrs = {header[i]: row[i].strip() for i in range(min(len(header), len(row)))}
        name = next((attrs[h] for h in header if h.lower() in _NAME_COLS and attrs.get(h)), "")
        if not name:
            continue
        desc = describe_history(attrs, rate_unit="ms")
        if desc:
            out[name.lower()] = desc
    return out
