"""Command-line entry point.

    python -m intouch_analyzer                       # open the GUI
    python -m intouch_analyzer <app folder>          # GUI, pre-filled and analysed
    python -m intouch_analyzer <app folder> --excel report.xlsx   # headless
    python -m intouch_analyzer --site "Baunton survey report"      # site survey
"""

from __future__ import annotations

import argparse
import os
import sys

from .analyzer import analyze
from .export import details_rows, export_csv, export_excel, summary_rows


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="intouch_analyzer",
                                description="Analyse IO and mimics of an InTouch application.")
    p.add_argument("app_folder", nargs="?", help="InTouch application folder")
    p.add_argument("--dbdump", action="append",
                   help="DBDump CSV file (repeatable). Default: auto-detect in folder")
    p.add_argument("--history", action="append",
                   help="Historian tag export with StorageType/StorageRate (repeatable)")
    p.add_argument("--excel", help="Write an Excel report (no GUI)")
    p.add_argument("--csv", help="Write CSV reports into this folder (no GUI)")
    p.add_argument("--no-other", action="store_true",
                   help="Only scan windows, not scripts/other files")
    p.add_argument("--summary", action="store_true", help="Print the report (no GUI)")
    p.add_argument("--details", action="store_true",
                   help="Also print the supporting details (with --summary/--excel/--csv)")
    p.add_argument("--site", help='Site survey, e.g. "Baunton survey report": copy the '
                   "site's InTouch application from the Sites folder and report on it")
    p.add_argument("--sites-root", help="Folder containing the site folders (04 Sites)")
    p.add_argument("--work-dir", help="Local InTouch working folder the application is copied to")
    p.add_argument("--app", type=int, default=1,
                   help="Which application to use when a site has several (1 = most windows)")
    args = p.parse_args(argv)

    if args.site:
        return _site_survey(args)

    if not (args.excel or args.csv or args.summary):
        from .gui import main as gui_main
        gui_main(args.app_folder, args.dbdump[0] if args.dbdump else None,
                 auto_run=bool(args.app_folder),
                 history=args.history[0] if args.history else None)
        return 0

    if not args.app_folder:
        p.error("app_folder is required for --excel/--csv/--summary")
    try:
        res = analyze(args.app_folder, args.dbdump, include_other_files=not args.no_other,
                      history_paths=args.history)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    _print_rows(summary_rows(res))
    if args.details:
        print()
        _print_rows(details_rows(res))
    for w in res.warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    if args.excel:
        export_excel(res, args.excel)
        print(f"Excel report written to {args.excel}")
    if args.csv:
        files = export_csv(res, args.csv)
        print(f"{len(files)} CSV files written to {args.csv}")
    return 0


def _print_rows(rows) -> None:
    for item, value in rows:
        lines = str(value).splitlines() or [""]
        print(f"{item:45} {lines[0]}")
        for line in lines[1:]:
            print(f"{'':45} {line}")


def _site_survey(args) -> int:
    from .sites import (app_label, copy_app, default_sites_root, default_work_root,
                        find_sites, locate_site_apps, report_path)
    root = args.sites_root or default_sites_root()
    matches = find_sites(root, args.site)
    if not matches:
        print(f"ERROR: no site matching '{args.site}' in {root}", file=sys.stderr)
        return 2
    if len(matches) > 1:
        print(f"ERROR: '{args.site}' matches several sites: {', '.join(matches)}. "
              "Be more specific.", file=sys.stderr)
        return 2
    try:
        found = locate_site_apps(root, matches[0], args.work_dir or default_work_root(),
                                 progress=print)
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for note in found.notes:
        print(f"WARNING: {note}", file=sys.stderr)
    if len(found.apps) > 1:
        print("Applications found (use --app N to choose):")
        for i, a in enumerate(found.apps, start=1):
            print(f"  {i}. {app_label(a, found.site_path)}")
    if not 1 <= args.app <= len(found.apps):
        print(f"ERROR: --app must be between 1 and {len(found.apps)}", file=sys.stderr)
        return 2
    try:
        local = copy_app(found, found.apps[args.app - 1], progress=print)
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Analysing {local}...")
    res = analyze(local, args.dbdump, include_other_files=not args.no_other,
                  history_paths=args.history)
    print()
    print(f"{found.site} HMI survey report")
    _print_rows(summary_rows(res))
    if args.details:
        print()
        _print_rows(details_rows(res))
    for w in res.warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    out = args.excel or report_path(found)
    try:
        export_excel(res, out)
        print(f"\nReport written to {out}")
    except RuntimeError as exc:  # openpyxl missing
        folder = args.csv or os.path.splitext(out)[0]
        export_csv(res, folder)
        print(f"\n{exc}\nCSV report written to {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
