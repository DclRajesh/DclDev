"""Tkinter GUI for the InTouch IO & mimic analyser."""

from __future__ import annotations

import os
import queue
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, List, Optional, Sequence

from . import __version__
from .analyzer import analyze
from .classify import (DEFAULT_RULES, MIMIC_TYPES, engineering_summary, load_overrides,
                       load_rules, override_key, save_settings, validate_rules)
from .export import (build_tables, details_rows, export_csv, export_excel, history_rows,
                     report_tsv, summary_rows)
from .models import AnalysisResult

ALL = "(All)"
AUTO = "(Auto)"
IO_CLASSES = ["AI", "AO", "DI", "DO", "PULSE", "MSG"]


class DataTable(ttk.Frame):
    """Treeview with scrollbars, click-to-sort headers and row filtering."""

    def __init__(self, master, columns: Sequence[str], widths: Optional[dict] = None,
                 on_select: Optional[Callable[[Sequence], None]] = None,
                 on_activate: Optional[Callable[[Sequence], None]] = None):
        super().__init__(master)
        self.columns = list(columns)
        self.rows: List[Sequence] = []
        self._shown: List[Sequence] = []
        self._sort = (None, False)
        self.on_select = on_select
        self.on_activate = on_activate

        self.tree = ttk.Treeview(self, columns=self.columns, show="headings",
                                 selectmode="browse")
        widths = widths or {}
        for c in self.columns:
            self.tree.heading(c, text=c, command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=widths.get(c, 120), stretch=True, anchor="w")
        ys = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._selected)
        self.tree.bind("<Double-1>", self._activated)
        self.tree.bind("<Return>", self._activated)

    def set_rows(self, rows: Sequence[Sequence]):
        self.rows = list(rows)
        self.show(self.rows)

    def show(self, rows: Sequence[Sequence]):
        col, desc = self._sort
        rows = list(rows)
        if col is not None:
            idx = self.columns.index(col)
            rows.sort(key=lambda r: _sort_key(r[idx]), reverse=desc)
        self._shown = rows
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(rows):
            self.tree.insert("", "end", iid=str(i), values=[_cell(v) for v in r])

    def sort_by(self, col: str):
        prev, desc = self._sort
        self._sort = (col, not desc if prev == col else False)
        for c in self.columns:
            arrow = ""
            if c == col:
                arrow = " ▼" if self._sort[1] else " ▲"
            self.tree.heading(c, text=c + arrow)
        self.show(self._shown)

    def selected_row(self) -> Optional[Sequence]:
        sel = self.tree.selection()
        return self._shown[int(sel[0])] if sel else None

    def _selected(self, _event=None):
        row = self.selected_row()
        if row is not None and self.on_select:
            self.on_select(row)

    def _activated(self, _event=None):
        row = self.selected_row()
        if row is not None and self.on_activate:
            self.on_activate(row)


def _cell(v) -> str:
    return "" if v is None else str(v)


def _sort_key(v):
    if isinstance(v, (int, float)):
        return (0, v, "")
    return (1, 0, str(v).lower())


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"InTouch IO & Mimic Analyser {__version__}")
        self.geometry("1280x800")
        self.minsize(900, 550)
        self.result: Optional[AnalysisResult] = None
        self.rules = load_rules()
        self.overrides: dict = {}
        self.eng = None
        self._queue: "queue.Queue" = queue.Queue()
        self._tables = {}

        self.app_var = tk.StringVar()
        self.dbdump_var = tk.StringVar()
        self.history_var = tk.StringVar()
        self.other_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Select an InTouch application folder and click Analyse.")

        self._build_toolbar()
        self._build_notebook()
        self._build_statusbar()

    # ---------------------------------------------------------------- layout
    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(8, 8, 8, 4))
        bar.pack(fill="x")
        ttk.Label(bar, text="InTouch application folder:").grid(row=0, column=0, sticky="w")
        ttk.Entry(bar, textvariable=self.app_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(bar, text="Browse...", command=self._browse_app).grid(row=0, column=2)

        ttk.Label(bar, text="DBDump CSV (optional):").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(bar, textvariable=self.dbdump_var).grid(row=1, column=1, sticky="ew", padx=4, pady=(4, 0))
        ttk.Button(bar, text="Browse...", command=self._browse_dbdump).grid(row=1, column=2, pady=(4, 0))

        ttk.Label(bar, text="Historian tag export (optional):").grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(bar, textvariable=self.history_var).grid(row=2, column=1, sticky="ew", padx=4, pady=(4, 0))
        ttk.Button(bar, text="Browse...", command=self._browse_history).grid(row=2, column=2, pady=(4, 0))

        opts = ttk.Frame(bar)
        opts.grid(row=0, column=3, rowspan=3, sticky="ns", padx=(12, 0))
        self.analyse_btn = ttk.Button(opts, text="Analyse", command=self._start_analysis)
        self.analyse_btn.pack(fill="x")
        ttk.Checkbutton(opts, text="Scan scripts/other files",
                        variable=self.other_var).pack(anchor="w", pady=(4, 0))
        ttk.Button(opts, text="Classification rules...",
                   command=self._edit_rules).pack(fill="x", pady=(4, 0))

        exp = ttk.Frame(bar)
        exp.grid(row=0, column=4, rowspan=3, sticky="ns", padx=(12, 0))
        self.excel_btn = ttk.Button(exp, text="Export Excel...", command=self._export_excel,
                                    state="disabled")
        self.excel_btn.pack(fill="x")
        self.csv_btn = ttk.Button(exp, text="Export CSV...", command=self._export_csv,
                                  state="disabled")
        self.csv_btn.pack(fill="x", pady=(4, 0))
        self.copy_btn = ttk.Button(exp, text="Copy report", command=self._copy_report,
                                   state="disabled")
        self.copy_btn.pack(fill="x", pady=(4, 0))
        bar.columnconfigure(1, weight=1)

    def _build_statusbar(self):
        bar = ttk.Frame(self, padding=(8, 2))
        bar.pack(fill="x", side="bottom")
        self.progress = ttk.Progressbar(bar, length=200, mode="determinate")
        self.progress.pack(side="right")
        ttk.Label(bar, textvariable=self.status_var).pack(side="left", fill="x")

    def _build_notebook(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=4)
        self.notebook = nb

        # Summary
        f = ttk.Frame(nb)
        nb.add(f, text="Summary")
        self.summary = DataTable(f, ["Item", "Value"], {"Item": 320, "Value": 800})
        self.summary.pack(fill="both", expand=True)
        ttk.Label(f, text="Historical logging (double-click a rate to list its tags):",
                  font=("TkDefaultFont", 9, "bold")).pack(anchor="w", pady=(6, 2))
        self.history_table = DataTable(f, ["History / Storage Rate", "Tags", "Breakdown"],
                                       {"History / Storage Rate": 320, "Tags": 80,
                                        "Breakdown": 700},
                                       on_activate=self._filter_by_history)
        self.history_table.tree.configure(height=6)
        self.history_table.pack(fill="x")

        # Tags
        f = ttk.Frame(nb)
        nb.add(f, text="Tags / IO")
        filt = ttk.Frame(f, padding=(0, 4))
        filt.pack(fill="x")
        self.tag_search = tk.StringVar()
        self.tag_cat = tk.StringVar(value=ALL)
        self.tag_access = tk.StringVar(value=ALL)
        self.tag_used = tk.StringVar(value=ALL)
        self.tag_hist = tk.StringVar(value=ALL)
        ttk.Label(filt, text="Search:").pack(side="left")
        e = ttk.Entry(filt, textvariable=self.tag_search, width=30)
        e.pack(side="left", padx=4)
        ttk.Label(filt, text="Type:").pack(side="left", padx=(8, 0))
        self.tag_cat_cb = ttk.Combobox(filt, textvariable=self.tag_cat, state="readonly", width=18)
        self.tag_cat_cb.pack(side="left", padx=4)
        ttk.Label(filt, text="Access name:").pack(side="left", padx=(8, 0))
        self.tag_access_cb = ttk.Combobox(filt, textvariable=self.tag_access, state="readonly", width=20)
        self.tag_access_cb.pack(side="left", padx=4)
        ttk.Label(filt, text="Used:").pack(side="left", padx=(8, 0))
        ttk.Combobox(filt, textvariable=self.tag_used, state="readonly", width=6,
                     values=[ALL, "Yes", "No"]).pack(side="left", padx=4)
        ttk.Label(filt, text="History:").pack(side="left", padx=(8, 0))
        self.tag_hist_cb = ttk.Combobox(filt, textvariable=self.tag_hist, state="readonly", width=22)
        self.tag_hist_cb.pack(side="left", padx=4)
        self.tag_count = ttk.Label(filt, text="")
        self.tag_count.pack(side="right")
        for var in (self.tag_search, self.tag_cat, self.tag_access, self.tag_used, self.tag_hist):
            var.trace_add("write", lambda *_: self._filter_tags())
        self.tags_table = DataTable(
            f, ["Tag", "Type", "Category", "Access Name", "Item", "Group", "Comment",
                "Alarm", "History", "Used", "# Windows", "Windows", "I/O Class"],
            {"Tag": 200, "Type": 100, "Category": 70, "Access Name": 110, "Item": 150,
             "Group": 90, "Comment": 200, "Alarm": 50, "History": 120, "Used": 45,
             "# Windows": 70, "Windows": 300, "I/O Class": 65},
            on_activate=self._show_tag_detail)
        self.tags_table.tree.configure(displaycolumns=(
            "Tag", "Type", "I/O Class", "Category", "Access Name", "Item", "Group", "Comment",
            "Alarm", "History", "Used", "# Windows", "Windows"))
        self.tags_table.pack(fill="both", expand=True)
        ttk.Label(f, text="Double-click a tag to see where it is used.",
                  foreground="gray").pack(anchor="w")

        # Access names
        f = ttk.Frame(nb)
        nb.add(f, text="Access Names")
        self.access_table = DataTable(
            f, ["Access Name", "Application", "Topic", "Advise Active", "Protocol",
                "Secondary Application", "Secondary Topic", "# Tags"],
            on_activate=self._filter_by_access)
        self.access_table.pack(fill="both", expand=True)
        ttk.Label(f, text="Double-click an access name to list its tags.",
                  foreground="gray").pack(anchor="w")

        # Mimics
        f = ttk.Frame(nb)
        nb.add(f, text="Mimics / Windows")
        pw = ttk.PanedWindow(f, orient="horizontal")
        pw.pack(fill="both", expand=True)
        left = ttk.Frame(pw)
        right = ttk.Frame(pw)
        pw.add(left, weight=2)
        pw.add(right, weight=3)
        filt = ttk.Frame(left, padding=(0, 4))
        filt.pack(fill="x")
        self.win_search = tk.StringVar()
        ttk.Label(filt, text="Search:").pack(side="left")
        ttk.Entry(filt, textvariable=self.win_search, width=20).pack(side="left", padx=4)
        self.win_type_filter = tk.StringVar(value=ALL)
        ttk.Label(filt, text="Type:").pack(side="left", padx=(8, 0))
        ttk.Combobox(filt, textvariable=self.win_type_filter, state="readonly", width=9,
                     values=[ALL] + list(MIMIC_TYPES)).pack(side="left", padx=4)
        for var in (self.win_search, self.win_type_filter):
            var.trace_add("write", lambda *_: self._filter_windows())
        setf = ttk.Frame(left, padding=(0, 0, 0, 4))
        setf.pack(fill="x")
        ttk.Label(setf, text="Set type of selected mimic:").pack(side="left")
        self.win_set_type = tk.StringVar(value=AUTO)
        ttk.Combobox(setf, textvariable=self.win_set_type, state="readonly", width=9,
                     values=[AUTO] + list(MIMIC_TYPES)).pack(side="left", padx=4)
        ttk.Button(setf, text="Apply", command=self._apply_mimic_type).pack(side="left")
        self.win_table = DataTable(
            left, ["Window", "Source", "# Tags", "# I/O Tags", "Access Names", "File",
                   "Mimic Type", "Type From"],
            {"Window": 180, "Source": 100, "# Tags": 55, "# I/O Tags": 65,
             "Access Names": 150, "File": 180, "Mimic Type": 80, "Type From": 85},
            on_select=self._show_window)
        self.win_table.tree.configure(displaycolumns=(
            "Window", "Mimic Type", "# Tags", "# I/O Tags", "Access Names", "Type From",
            "Source", "File"))
        self.win_table.pack(fill="both", expand=True)
        self.win_info = tk.Text(right, height=5, wrap="word", relief="flat",
                                background=self.cget("background"))
        self.win_info.pack(fill="x", pady=(4, 4))
        self.win_info.configure(state="disabled")
        self.win_tags = DataTable(
            right, ["Tag", "Type", "Access Name", "Item", "References", "Comment"],
            {"Tag": 180, "Type": 90, "Access Name": 110, "Item": 140,
             "References": 75, "Comment": 200},
            on_activate=lambda row: self._show_tag_detail(row))
        self.win_tags.pack(fill="both", expand=True)

        # Cross reference
        f = ttk.Frame(nb)
        nb.add(f, text="Window-Tag Map")
        filt = ttk.Frame(f, padding=(0, 4))
        filt.pack(fill="x")
        self.map_search = tk.StringVar()
        ttk.Label(filt, text="Search:").pack(side="left")
        ttk.Entry(filt, textvariable=self.map_search, width=30).pack(side="left", padx=4)
        self.map_search.trace_add("write", lambda *_: self._filter_map())
        self.map_table = DataTable(
            f, ["Window/File", "Source", "Tag", "Type", "Access Name", "Item", "References"])
        self.map_table.pack(fill="both", expand=True)

        # Issues
        f = ttk.Frame(nb)
        nb.add(f, text="Issues")
        self.issues_table = DataTable(f, ["Severity", "Category", "Item", "Detail"],
                                      {"Severity": 80, "Category": 220, "Item": 220,
                                       "Detail": 600})
        self.issues_table.pack(fill="both", expand=True)

    # --------------------------------------------------------------- actions
    def _browse_app(self):
        path = filedialog.askdirectory(title="Select InTouch application folder")
        if path:
            self.app_var.set(path)

    def _browse_history(self):
        path = filedialog.askopenfilename(
            title="Select Historian tag export",
            filetypes=[("CSV / text", "*.csv *.txt"), ("All files", "*.*")],
            initialdir=self.app_var.get() or None)
        if path:
            self.history_var.set(path)

    def _browse_dbdump(self):
        path = filedialog.askopenfilename(
            title="Select DBDump CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialdir=self.app_var.get() or None)
        if path:
            self.dbdump_var.set(path)

    def _start_analysis(self):
        app = self.app_var.get().strip()
        if not app or not os.path.isdir(app):
            messagebox.showerror("Analyse", "Please select a valid InTouch application folder.")
            return
        dbdump = self.dbdump_var.get().strip()
        if dbdump and not os.path.isfile(dbdump):
            messagebox.showerror("Analyse", f"DBDump file not found:\n{dbdump}")
            return
        history = self.history_var.get().strip()
        if history and not os.path.isfile(history):
            messagebox.showerror("Analyse", f"Historian file not found:\n{history}")
            return
        self.analyse_btn.configure(state="disabled")
        self.status_var.set("Analysing...")

        def worker():
            try:
                res = analyze(app, [dbdump] if dbdump else None,
                              include_other_files=self.other_var.get(),
                              progress=lambda i, n, m: self._queue.put(("progress", i, n, m)),
                              history_paths=[history] if history else None)
                self._queue.put(("done", res))
            except Exception as exc:  # noqa: BLE001 - report any failure to the user
                self._queue.put(("error", exc, traceback.format_exc()))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._poll)

    def _poll(self):
        try:
            while True:
                msg = self._queue.get_nowait()
                if msg[0] == "progress":
                    _, i, n, text = msg
                    self.progress.configure(maximum=max(n, 1), value=i)
                    self.status_var.set(text)
                elif msg[0] == "done":
                    self._on_done(msg[1])
                    return
                elif msg[0] == "error":
                    self.analyse_btn.configure(state="normal")
                    self.status_var.set("Analysis failed.")
                    messagebox.showerror("Analysis failed", f"{msg[1]}\n\n{msg[2]}")
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _on_done(self, res: AnalysisResult):
        self.result = res
        self.analyse_btn.configure(state="normal")
        self.excel_btn.configure(state="normal")
        self.csv_btn.configure(state="normal")
        self.copy_btn.configure(state="normal")
        self.overrides = load_overrides(res.app_path)
        self.tag_search.set("")
        self.tag_cat.set(ALL)
        self.tag_access.set(ALL)
        self.tag_used.set(ALL)
        self.win_search.set("")
        self.win_type_filter.set(ALL)
        self._refresh()
        self.status_var.set(
            f"Analysed {os.path.basename(res.app_path)}: {len(res.tags)} tags, "
            f"{len(res.io_tags)} I/O, {len(res.access_names)} access names, "
            f"{len(res.windows)} windows.")

    def _refresh(self):
        """(Re)build all views from the result, rules and mimic overrides."""
        res = self.result
        if not res:
            return
        self.eng = engineering_summary(res, self.rules, self.overrides)
        self._tables = build_tables(res, self.eng)

        summary = []
        for rows in (summary_rows(res, self.eng),
                     [("", ""), ("Details", "")] + details_rows(res, self.eng)):
            for item, value in rows:
                lines = str(value).split("\n")
                summary.append((item, lines[0]))
                summary.extend(("", line) for line in lines[1:])
        self.summary.set_rows(summary + [("Warning", w) for w in res.warnings])
        hist = history_rows(res)
        self.history_table.set_rows(hist)
        self.tag_hist_cb.configure(values=[ALL, "Logged (any)"] + [d for d, _, _ in hist]
                                   + ["Not logged"])
        if self.tag_hist.get() not in self.tag_hist_cb.cget("values"):
            self.tag_hist.set(ALL)
        cats = sorted({t.category for t in res.tags.values()})
        types = sorted(res.type_counts())
        self.tag_cat_cb.configure(values=[ALL] + cats + IO_CLASSES +
                                  [t for t in types if t not in cats])
        self.tag_access_cb.configure(
            values=[ALL] + sorted({t.access_name for t in res.tags.values() if t.access_name},
                                  key=str.lower))
        self.tags_table.set_rows([r[:12] + (r[13],) for r in self._tables["Tags"][1]])
        self._filter_tags()
        self.access_table.set_rows(self._tables["Access Names"][1])
        self.win_table.set_rows([(r[0], r[1], r[3], r[4], r[5], r[2], r[9], r[10])
                                 for r in self._tables["Windows"][1]])
        self._filter_windows()
        self.map_table.set_rows(self._tables["Window-Tag Map"][1])
        self._filter_map()
        self.issues_table.set_rows(self._tables["Issues"][1])
        self.notebook.tab(5, text=f"Issues ({len(self._tables['Issues'][1])})")

    # --------------------------------------------------------------- filters
    def _filter_tags(self):
        text = self.tag_search.get().strip().lower()
        cat, acc, used = self.tag_cat.get(), self.tag_access.get(), self.tag_used.get()
        hist = self.tag_hist.get()
        rows = [r for r in self.tags_table.rows
                if (cat == ALL or cat in (r[1], r[2], r[12]))
                and (hist == ALL or r[8] == hist
                     or (hist == "Logged (any)" and r[8] != "Not logged"))
                and (acc == ALL or r[3].lower() == acc.lower())
                and (used == ALL or r[9] == used)
                and (not text or any(text in str(v).lower() for v in (r[0], r[3], r[4], r[5], r[6])))]
        self.tags_table.show(rows)
        self.tag_count.configure(text=f"{len(rows)} of {len(self.tags_table.rows)} tags")

    def _filter_windows(self):
        text = self.win_search.get().strip().lower()
        wtype = self.win_type_filter.get()
        self.win_table.show([r for r in self.win_table.rows
                             if (not text or text in r[0].lower() or text in r[5].lower())
                             and (wtype == ALL or r[6] == wtype)])

    def _filter_map(self):
        text = self.map_search.get().strip().lower()
        self.map_table.show([r for r in self.map_table.rows
                             if not text or any(text in str(v).lower() for v in r[:6])])

    def _filter_by_history(self, row):
        self.tag_search.set("")
        self.tag_cat.set(ALL)
        self.tag_access.set(ALL)
        self.tag_used.set(ALL)
        self.tag_hist.set(row[0])
        self.notebook.select(1)

    def _filter_by_access(self, row):
        self.tag_search.set("")
        self.tag_cat.set(ALL)
        self.tag_used.set(ALL)
        self.tag_hist.set(ALL)
        self.tag_access.set(row[0])
        self.notebook.select(1)

    # ------------------------------------------------------ classification
    def _apply_mimic_type(self):
        row = self.win_table.selected_row()
        if not self.result or row is None:
            messagebox.showinfo("Mimic type", "Select a mimic in the list first.")
            return
        src = next((s for s in self.result.windows
                    if s.name == row[0] and s.path == row[5]), None)
        if src is None:
            return
        key = override_key(src)
        if self.win_set_type.get() == AUTO:
            self.overrides.pop(key, None)
        else:
            self.overrides[key] = self.win_set_type.get()
        try:
            save_settings(app_path=self.result.app_path, overrides=self.overrides)
        except OSError as exc:
            messagebox.showwarning("Mimic type", f"Could not save the override:\n{exc}")
        self._refresh()
        self.status_var.set(f"Mimic '{src.name}' set to {self.win_set_type.get()}")

    def _edit_rules(self):
        RulesDialog(self, self.rules, self._rules_saved)

    def _rules_saved(self, rules):
        self.rules = rules
        try:
            save_settings(rules=rules)
        except OSError as exc:
            messagebox.showwarning("Rules", f"Could not save the rules:\n{exc}")
        self._refresh()

    # --------------------------------------------------------------- details
    def _show_window(self, row):
        res = self.result
        if not res:
            return
        src = next((s for s in res.windows if s.name == row[0] and s.path == row[5]), None)
        if src is None:
            return
        rows = []
        for key, count in src.tag_refs.items():
            t = res.tags.get(key)
            rows.append((t.name if t else key, t.tag_type if t else "",
                         t.access_name if t else "", t.item_name if t else "",
                         count, t.comment if t else ""))
        rows.sort(key=lambda r: r[0].lower())
        self.win_tags.set_rows(rows)
        self.win_set_type.set(self.overrides.get(override_key(src), AUTO))
        info = [f"Window: {src.name}   ({src.kind}, {src.path}, {src.size:,} bytes)"]
        if src.opens_windows:
            info.append("Opens windows: " + ", ".join(sorted(src.opens_windows)))
        if src.remote_refs:
            info.append("Remote references: " + ", ".join(sorted(src.remote_refs)))
        if src.unresolved:
            info.append("Undefined tag references: " + ", ".join(sorted(src.unresolved)))
        self.win_info.configure(state="normal")
        self.win_info.delete("1.0", "end")
        self.win_info.insert("1.0", "\n".join(info))
        self.win_info.configure(state="disabled")

    def _show_tag_detail(self, row):
        res = self.result
        if not res:
            return
        t = res.find_tag(str(row[0]))
        if t is None:
            return
        users = res.tag_usage().get(t.key, [])
        top = tk.Toplevel(self)
        top.title(f"Tag: {t.name}")
        top.geometry("640x480")
        top.transient(self)
        text = tk.Text(top, wrap="word")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        lines = [f"Tag:          {t.name}",
                 f"Type:         {t.tag_type}",
                 f"Group:        {t.group}",
                 f"Comment:      {t.comment}",
                 f"Access name:  {t.access_name}",
                 f"Item:         {t.item_name}",
                 f"Alarmed:      {'Yes' if t.has_alarm else 'No'}",
                 f"History:      {t.history or 'Not logged'}",
                 f"I/O class:    {(self.eng.io_classes.get(t.key) if self.eng else '') or '-'}",
                 "",
                 f"Referenced in {len(users)} window(s)/file(s):"]
        for s in sorted(users, key=lambda s: (not s.is_window, s.name.lower())):
            lines.append(f"  - {s.name}  [{s.kind}]  x{s.tag_refs[t.key]}")
        if t.attributes:
            lines += ["", "All DBDump attributes:"]
            lines += [f"  {k}: {v}" for k, v in t.attributes.items() if v]
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")
        ttk.Button(top, text="Close", command=top.destroy).pack(pady=(0, 8))

    # ---------------------------------------------------------------- export
    def _default_name(self) -> str:
        if not self.result:
            return "intouch_analysis"
        return os.path.basename(self.result.app_path.rstrip("\\/")) + "_analysis"

    def _export_excel(self):
        if not self.result:
            return
        path = filedialog.asksaveasfilename(
            title="Export to Excel", defaultextension=".xlsx",
            initialfile=self._default_name() + ".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")])
        if not path:
            return
        try:
            export_excel(self.result, path, self.eng)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Export failed", str(exc))
            return
        self.status_var.set(f"Exported {path}")
        messagebox.showinfo("Export", f"Report saved to:\n{path}")

    def _copy_report(self):
        if not self.result:
            return
        self.clipboard_clear()
        self.clipboard_append(report_tsv(self.result, self.eng))
        self.status_var.set("Report copied to the clipboard - paste it into Excel or Word.")

    def _export_csv(self):
        if not self.result:
            return
        folder = filedialog.askdirectory(title="Select folder for CSV files")
        if not folder:
            return
        folder = os.path.join(folder, self._default_name())
        try:
            files = export_csv(self.result, folder, self.eng)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Export failed", str(exc))
            return
        self.status_var.set(f"Exported {len(files)} CSV files to {folder}")
        messagebox.showinfo("Export", f"{len(files)} CSV files saved to:\n{folder}")


class RulesDialog(tk.Toplevel):
    """Edit the regular expressions used to classify mimics and I/O points."""

    LABELS = {
        "popup_windows": "Popup (on top) mimic names",
        "overlay_windows": "Overlay mimic names",
        "other_windows": "Other / excluded mimic names",
        "pulse_tags": "PULSE I/O (discrete)",
        "output_tags": "Output I/O (DO / AO)",
        "input_tags": "Input I/O (DI / AI)",
    }

    def __init__(self, master, rules, on_save):
        super().__init__(master)
        self.title("Classification rules")
        self.geometry("820x560")
        self.transient(master)
        self.on_save = on_save
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        ttk.Label(body, wraplength=780, justify="left", text=(
            "Rules are case-insensitive regular expressions (Python syntax). "
            "Mimic rules are matched against the window name and are only used when the "
            "window type is not known from an XML export or set manually. I/O rules are "
            "matched against the tag name, comment and item; tags matching none of them "
            "are inputs unless ReadOnly = No and a script writes to them.")).pack(anchor="w")
        grid = ttk.Frame(body)
        grid.pack(fill="x", pady=8)
        self.vars = {}
        for i, (key, label) in enumerate(self.LABELS.items()):
            ttk.Label(grid, text=label + ":").grid(row=i, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=str(rules.get(key, "")))
            ttk.Entry(grid, textvariable=var).grid(row=i, column=1, sticky="ew", padx=6)
            self.vars[key] = var
        grid.columnconfigure(1, weight=1)
        self.writes_var = tk.BooleanVar(value=bool(rules.get("script_writes_are_outputs")))
        ttk.Checkbutton(body, variable=self.writes_var, text=(
            "Tags written by scripts (Tag = ...) are outputs")).pack(anchor="w")
        ttk.Label(body, text="Measurement categories for historisation "
                  "(one per line: Name = regex, first match wins):").pack(anchor="w", pady=(8, 2))
        self.cats = tk.Text(body, height=10, wrap="none")
        self.cats.pack(fill="both", expand=True)
        self._set_categories(rules.get("measurement_categories", []))
        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Restore defaults", command=self._defaults).pack(side="left")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Save", command=self._save).pack(side="right", padx=6)

    def _set_categories(self, cats):
        self.cats.delete("1.0", "end")
        self.cats.insert("1.0", "\n".join(f"{n} = {p}" for n, p in cats))

    def _defaults(self):
        for key, var in self.vars.items():
            var.set(DEFAULT_RULES[key])
        self.writes_var.set(DEFAULT_RULES["script_writes_are_outputs"])
        self._set_categories(DEFAULT_RULES["measurement_categories"])

    def _save(self):
        rules = dict(DEFAULT_RULES)
        rules.update({k: v.get().strip() for k, v in self.vars.items()})
        rules["script_writes_are_outputs"] = self.writes_var.get()
        cats = []
        for line in self.cats.get("1.0", "end").splitlines():
            if "=" in line:
                name, pat = line.split("=", 1)
                if name.strip() and pat.strip():
                    cats.append([name.strip(), pat.strip()])
        rules["measurement_categories"] = cats
        errors = validate_rules(rules)
        if errors:
            messagebox.showerror("Invalid rule", "\n".join(errors), parent=self)
            return
        self.destroy()
        self.on_save(rules)


def main(app_path: Optional[str] = None, dbdump: Optional[str] = None,
         auto_run: bool = False, history: Optional[str] = None):
    app = App()
    if app_path:
        app.app_var.set(app_path)
    if dbdump:
        app.dbdump_var.set(dbdump)
    if history:
        app.history_var.set(history)
    if app_path and auto_run:
        app.after(200, app._start_analysis)
    app.mainloop()


if __name__ == "__main__":
    main()
