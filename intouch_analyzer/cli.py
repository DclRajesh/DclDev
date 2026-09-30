"""Command-line entry point.

    python -m intouch_analyzer                       # open the GUI
    python -m intouch_analyzer <app folder>          # GUI, pre-filled and analysed
    python -m intouch_analyzer <app folder> --excel report.xlsx   # headless
"""

from __future__ import annotations

import argparse
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
    args = p.parse_args(argv)

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

    def show(rows):
        for item, value in rows:
            lines = str(value).splitlines() or [""]
            print(f"{item:45} {lines[0]}")
            for line in lines[1:]:
                print(f"{'':45} {line}")

    show(summary_rows(res))
    if args.details:
        print()
        show(details_rows(res))
    for w in res.warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    if args.excel:
        export_excel(res, args.excel)
        print(f"Excel report written to {args.excel}")
    if args.csv:
        files = export_csv(res, args.csv)
        print(f"{len(files)} CSV files written to {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
