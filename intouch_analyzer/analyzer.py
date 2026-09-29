"""Scan an InTouch application folder and cross-reference tags and windows."""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from .binscan import RefMatcher, extract_strings
from .dbdump import looks_like_dbdump, parse_dbdump, parse_tagname_x
from .history import describe_history, parse_history_file
from .models import AnalysisResult, Source

ProgressFn = Callable[[int, int, str], None]

# Files that never contain tag references (history, media, binaries, archives).
SKIP_EXTENSIONS = {
    ".lgh", ".idx", ".bmp", ".jpg", ".jpeg", ".png", ".gif", ".ico", ".tif",
    ".tiff", ".wmf", ".emf", ".exe", ".dll", ".ocx", ".zip", ".7z", ".rar",
    ".cab", ".aapkg", ".aaapp", ".pdf", ".log", ".wav", ".mp3", ".avi", ".mp4",
    ".chm", ".hlp", ".msi", ".lnk", ".db", ".mdb", ".ldf", ".mdf",
}
MAX_OTHER_FILE_SIZE = 20 * 1024 * 1024
_NAME_ATTRS = ("name", "windowname", "title")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _texts_of(elem: ET.Element) -> Iterable[str]:
    for e in elem.iter():
        for v in e.attrib.values():
            if v:
                yield v
        if e.text and e.text.strip():
            yield e.text
        if e.tail and e.tail.strip():
            yield e.tail


def _xml_windows(root: ET.Element) -> List[Tuple[str, ET.Element, str]]:
    """Find window elements (outermost ones only) in an exported XML file.

    Returns (name, element, window type) with the type ('Replace', 'Overlay',
    'Popup') taken from a *Type attribute or child element when present."""
    found: List[Tuple[str, ET.Element, str]] = []

    def visit(elem: ET.Element):
        local = _local(elem.tag)
        if "window" in local:
            attrs = {k.rsplit("}", 1)[-1].lower(): v for k, v in elem.attrib.items()}
            name = next((attrs[a] for a in _NAME_ATTRS if attrs.get(a)), "")
            if not name:
                child = next((c for c in elem if _local(c.tag) in _NAME_ATTRS
                              and (c.text or "").strip()), None)
                name = child.text.strip() if child is not None else ""
            if name:
                found.append((name, elem, _window_type(elem, attrs)))
                return
        for child in elem:
            visit(child)

    visit(root)
    return found


def _window_type(elem: ET.Element, attrs) -> str:
    candidates = [v for k, v in attrs.items() if "type" in k]
    candidates += [(c.text or "") for c in elem if "type" in _local(c.tag)]
    for v in candidates:
        if re.search(r"(?i)replace|overlay|pop-?up", v or ""):
            return v.strip()
    return ""


def _walk(app_path: str) -> List[str]:
    files = []
    for dirpath, dirnames, filenames in os.walk(app_path):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for f in sorted(filenames):
            files.append(os.path.join(dirpath, f))
    return files


def analyze(app_path: str,
            dbdump_paths: Optional[Sequence[str]] = None,
            include_other_files: bool = True,
            progress: Optional[ProgressFn] = None,
            history_paths: Optional[Sequence[str]] = None) -> AnalysisResult:
    """Analyse an InTouch application folder.

    ``dbdump_paths``: DBDump CSV file(s). When omitted, CSV files in the
    application folder that look like DBDump output are used, and failing
    that tag names are extracted heuristically from ``tagname.x``.

    ``history_paths``: optional Historian tag export(s) giving each tag's
    storage type/rate; these override the logging settings in the DBDump.
    """
    if not os.path.isdir(app_path):
        raise FileNotFoundError(f"Application folder not found: {app_path}")
    result = AnalysisResult(app_path=os.path.abspath(app_path))
    report = progress or (lambda i, n, msg: None)
    files = _walk(app_path)

    # ---- 1. Tag dictionary ------------------------------------------------
    report(0, len(files), "Reading tag database...")
    if dbdump_paths:
        csvs = list(dbdump_paths)
    else:
        csvs = [f for f in files if f.lower().endswith(".csv") and looks_like_dbdump(f)]
    # Tag DB / history inputs are not scanned for references.
    tag_db_files = {os.path.abspath(f) for f in list(csvs) + list(history_paths or [])}
    for path in csvs:
        tags, access, warns = parse_dbdump(path)
        result.warnings.extend(warns)
        for t in tags:
            if t.key in result.tags:
                result.warnings.append(f"Duplicate tag '{t.name}' in {os.path.basename(path)}")
            result.tags[t.key] = t
        for a in access:
            result.access_names[a.key] = a
    if csvs:
        result.tag_db_source = "DBDump: " + ", ".join(os.path.basename(p) for p in csvs)
    else:
        tagx = next((f for f in files if os.path.basename(f).lower() == "tagname.x"), None)
        if tagx:
            for t in parse_tagname_x(tagx):
                result.tags[t.key] = t
            result.tag_db_source = "tagname.x (heuristic)"
            result.warnings.append(
                "No DBDump CSV found - tag names were extracted heuristically from "
                "tagname.x, so tag types, access names and I/O addresses are unknown and "
                "some names may be false positives. Run DBDump for accurate results.")
        else:
            result.tag_db_source = "none"
            result.warnings.append(
                "No tag database found (no DBDump CSV and no tagname.x). "
                "Window/tag cross-reference is not possible.")

    _apply_history(result, csvs, history_paths or [])

    matcher = RefMatcher(result.tags.keys(), result.access_names.keys())

    # ---- 2. Windows and other files -------------------------------------
    for i, path in enumerate(files, start=1):
        base = os.path.basename(path)
        stem, ext = os.path.splitext(base)
        ext = ext.lower()
        rel = os.path.relpath(path, app_path)
        if os.path.abspath(path) in tag_db_files or base.lower() == "tagname.x":
            continue
        report(i, len(files), f"Scanning {rel}")
        try:
            size = os.path.getsize(path)
            if ext == ".win":
                with open(path, "rb") as fh:
                    data = fh.read()
                result.sources.append(_make_source(
                    matcher, stem, rel, "window-binary", size, extract_strings(data)))
            elif ext == ".xml":
                _scan_xml(result, matcher, path, rel, size, include_other_files)
            elif (include_other_files and ext not in SKIP_EXTENSIONS
                  and ext != ".csv" and 0 < size <= MAX_OTHER_FILE_SIZE):
                with open(path, "rb") as fh:
                    data = fh.read()
                src = _make_source(matcher, base, rel, "script/other", size,
                                   extract_strings(data))
                if src.tag_refs or src.opens_windows:
                    src.unresolved.clear()  # too noisy for arbitrary files
                    result.sources.append(src)
        except OSError as exc:
            result.warnings.append(f"Could not read {rel}: {exc}")

    if not result.windows:
        result.warnings.append("No window files (*.win or exported XML windows) were found.")
    report(len(files), len(files), "Done")
    return result


def _apply_history(result: AnalysisResult, dbdumps: Sequence[str],
                   history_paths: Sequence[str]) -> None:
    for t in result.tags.values():
        t.history = describe_history(t.attributes)
    if dbdumps:
        result.history_source = "DBDump (Logged / LogDeadband)"
    for path in history_paths:
        entries = parse_history_file(path)
        unmatched = 0
        for name, desc in entries.items():
            # Historian names may carry a node/prefix: "Node.TagName".
            tag = result.tags.get(name) or result.tags.get(name.rsplit(".", 1)[-1])
            if tag:
                tag.history = desc
            else:
                unmatched += 1
        base = os.path.basename(path)
        result.history_source = ", ".join(filter(None, [result.history_source, base]))
        if not entries:
            result.warnings.append(f"{base}: no tag storage settings found")
        elif unmatched:
            result.warnings.append(
                f"{base}: {unmatched} of {len(entries)} historised tags are not in the "
                "InTouch tag database")


def _make_source(matcher: RefMatcher, name: str, rel: str, kind: str, size: int,
                 texts: Iterable[str], window_type: str = "") -> Source:
    refs, unresolved, remote, opens, writes = matcher.scan(texts)
    return Source(name=name, path=rel, kind=kind, size=size, tag_refs=Counter(refs),
                  unresolved=unresolved, remote_refs=remote, opens_windows=opens,
                  tag_writes=writes, window_type=window_type)


def _scan_xml(result: AnalysisResult, matcher: RefMatcher, path: str, rel: str,
              size: int, include_other: bool) -> None:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        result.warnings.append(f"{rel}: invalid XML ({exc}), scanned as plain text")
        with open(path, "rb") as fh:
            data = fh.read()
        src = _make_source(matcher, os.path.basename(path), rel, "script/other", size,
                           extract_strings(data))
        if src.tag_refs:
            result.sources.append(src)
        return
    windows = _xml_windows(root)
    for name, elem, wtype in windows:
        result.sources.append(_make_source(matcher, name, rel, "window-xml", size,
                                           _texts_of(elem), wtype))
    if not windows and include_other:
        src = _make_source(matcher, os.path.basename(path), rel, "script/other", size,
                           _texts_of(root))
        if src.tag_refs:
            result.sources.append(src)
