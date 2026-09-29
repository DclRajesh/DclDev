"""Report tables and export to Excel (openpyxl) or CSV."""

from __future__ import annotations

import csv
import os
from typing import Dict, List, Sequence, Tuple

from .models import AnalysisResult

Table = Tuple[List[str], List[Sequence]]


def _yn(flag: bool) -> str:
    return "Yes" if flag else "No"


def summary_rows(r: AnalysisResult) -> List[Tuple[str, str]]:
    per_access = r.tags_per_access_name()
    rows = [
        ("Application folder", r.app_path),
        ("Tag database source", r.tag_db_source),
        ("Total tags", str(len(r.tags))),
        ("I/O tags", str(len(r.io_tags))),
        ("Access names", str(len(r.access_names))),
        ("Windows (mimics)", str(len(r.windows))),
        ("Other files referencing tags", str(len(r.other_sources))),
        ("Tags used in windows/scripts", str(len(r.tags) - len(r.unused_tags()))),
        ("Unused tags", str(len(r.unused_tags()))),
        ("Duplicate I/O addresses", str(len(r.duplicate_io_addresses()))),
        ("Alarmed tags", str(sum(1 for t in r.tags.values() if t.has_alarm))),
        ("Logged tags", str(sum(1 for t in r.tags.values() if t.is_logged))),
    ]
    for ttype, n in sorted(r.type_counts().items()):
        rows.append((f"  Tag type: {ttype}", str(n)))
    for acc in sorted(r.access_names.values(), key=lambda a: a.key):
        rows.append((f"  Access name: {acc.name}", f"{per_access.get(acc.key, 0)} tags"))
    return rows


def build_tables(r: AnalysisResult) -> Dict[str, Table]:
    usage = r.tag_usage()
    win_usage = r.window_usage()
    per_access = r.tags_per_access_name()
    tables: Dict[str, Table] = {}

    tables["Summary"] = (["Item", "Value"], summary_rows(r))

    tag_rows = []
    for t in sorted(r.tags.values(), key=lambda t: t.key):
        wins = win_usage.get(t.key, [])
        others = [s.name for s in usage.get(t.key, []) if not s.is_window]
        tag_rows.append((t.name, t.tag_type, t.category, t.access_name, t.item_name,
                         t.group, t.comment, _yn(t.has_alarm), _yn(t.is_logged),
                         _yn(t.key in usage), len(wins), "; ".join(sorted(wins)),
                         "; ".join(sorted(others))))
    tables["Tags"] = (["Tag", "Type", "Category", "Access Name", "Item", "Group",
                       "Comment", "Alarm", "Logged", "Used", "# Windows",
                       "Windows", "Other Files"], tag_rows)

    tables["Access Names"] = (
        ["Access Name", "Application", "Topic", "Advise Active", "Protocol",
         "Secondary Application", "Secondary Topic", "# Tags"],
        [(a.name, a.application, a.topic, a.advise_active, a.protocol,
          a.sec_application, a.sec_topic, per_access.get(a.key, 0))
         for a in sorted(r.access_names.values(), key=lambda a: a.key)])

    win_rows = []
    for w in sorted(r.windows, key=lambda w: w.name.lower()):
        io = [k for k in w.tag_refs if k in r.tags and r.tags[k].is_io]
        accesses = sorted({r.tags[k].access_name for k in io if r.tags[k].access_name})
        win_rows.append((w.name, w.kind, w.path, len(w.tag_refs), len(io),
                         "; ".join(accesses), "; ".join(sorted(w.opens_windows)),
                         "; ".join(sorted(w.remote_refs)), len(w.unresolved)))
    tables["Windows"] = (["Window", "Source", "File", "# Tags", "# I/O Tags",
                          "Access Names", "Opens Windows", "Remote References",
                          "# Undefined Refs"], win_rows)

    wt_rows = []
    for w in sorted(r.sources, key=lambda s: (not s.is_window, s.name.lower())):
        for key, count in sorted(w.tag_refs.items()):
            t = r.tags.get(key)
            wt_rows.append((w.name, w.kind, t.name if t else key,
                            t.tag_type if t else "", t.access_name if t else "",
                            t.item_name if t else "", count))
    tables["Window-Tag Map"] = (["Window/File", "Source", "Tag", "Type",
                                 "Access Name", "Item", "References"], wt_rows)

    dup_rows = [(addr, t.name, t.tag_type)
                for addr, dup in r.duplicate_io_addresses() for t in dup]
    tables["Duplicate IO"] = (["I/O Address", "Tag", "Type"], dup_rows)

    tables["Issues"] = (["Severity", "Category", "Item", "Detail"],
                        [(i.severity, i.category, i.item, i.detail) for i in r.issues()])
    return tables


def export_excel(r: AnalysisResult, path: str) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError("Excel export needs openpyxl: pip install openpyxl") from exc

    wb = Workbook()
    wb.remove(wb.active)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="305496")
    for name, (headers, rows) in build_tables(r).items():
        ws = wb.create_sheet(name[:31])
        ws.append(headers)
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
        for row in rows:
            ws.append(list(row))
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = ws.dimensions
        for idx, h in enumerate(headers, start=1):
            width = max([len(str(h))] + [len(str(row[idx - 1])) for row in rows[:500]])
            ws.column_dimensions[get_column_letter(idx)].width = min(max(width + 2, 8), 60)
    wb.save(path)


def export_csv(r: AnalysisResult, folder: str) -> List[str]:
    os.makedirs(folder, exist_ok=True)
    written = []
    for name, (headers, rows) in build_tables(r).items():
        path = os.path.join(folder, name.replace(" ", "_").lower() + ".csv")
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(headers)
            w.writerows(rows)
        written.append(path)
    return written
