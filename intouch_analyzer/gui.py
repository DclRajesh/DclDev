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
from .export import build_tables, export_csv, export_excel, history_rows, summary_rows
from .models import AnalysisResult

ALL = "(All)"


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

        exp = ttk.Frame(bar)
        exp.grid(row=0, column=4, rowspan=3, sticky="ns", padx=(12, 0))
        self.excel_btn = ttk.Button(exp, text="Export Excel...", command=self._export_excel,
                                    state="disabled")
        self.excel_btn.pack(fill="x")
        self.csv_btn = ttk.Button(exp, text="Export CSV...", command=self._export_csv,
                                  state="disabled")
        self.csv_btn.pack(fill="x", pady=(4, 0))
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
                "Alarm", "History", "Used", "# Windows", "Windows"],
            {"Tag": 200, "Type": 100, "Category": 70, "Access Name": 110, "Item": 150,
             "Group": 90, "Comment": 200, "Alarm": 50, "History": 120, "Used": 45,
             "# Windows": 70, "Windows": 300},
            on_activate=self._show_tag_detail)
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
        ttk.Entry(filt, textvariable=self.win_search, width=30).pack(side="left", padx=4)
        self.win_search.trace_add("write", lambda *_: self._filter_windows())
        self.win_table = DataTable(
            left, ["Window", "Source", "# Tags", "# I/O Tags", "Access Names", "File"],
            {"Window": 180, "Source": 100, "# Tags": 60, "# I/O Tags": 70,
             "Access Names": 150, "File": 180},
            on_select=self._show_window)
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
        self._tables = build_tables(res)

        self.summary.set_rows(summary_rows(res) +
                              [("Warning", w) for w in res.warnings])
        hist = history_rows(res)
        self.history_table.set_rows(hist)
        self.tag_hist_cb.configure(values=[ALL, "Logged (any)"] + [d for d, _, _ in hist]
                                   + ["Not logged"])
        self.tag_hist.set(ALL)
        cats = sorted({t.category for t in res.tags.values()})
        types = sorted(res.type_counts())
        self.tag_cat_cb.configure(values=[ALL] + cats +
                                  [t for t in types if t not in cats])
        self.tag_access_cb.configure(
            values=[ALL] + sorted({t.access_name for t in res.tags.values() if t.access_name},
                                  key=str.lower))
        self.tag_search.set("")
        self.tag_cat.set(ALL)
        self.tag_access.set(ALL)
        self.tag_used.set(ALL)
        self.tags_table.set_rows([r[:12] for r in self._tables["Tags"][1]])
        self._filter_tags()
        self.access_table.set_rows(self._tables["Access Names"][1])
        self.win_table.set_rows([(r[0], r[1], r[3], r[4], r[5], r[2])
                                 for r in self._tables["Windows"][1]])
        self._filter_windows()
        self.map_table.set_rows(self._tables["Window-Tag Map"][1])
        self._filter_map()
        self.issues_table.set_rows(self._tables["Issues"][1])
        self.notebook.tab(5, text=f"Issues ({len(self._tables['Issues'][1])})")
        self.status_var.set(
            f"Analysed {os.path.basename(res.app_path)}: {len(res.tags)} tags, "
            f"{len(res.io_tags)} I/O, {len(res.access_names)} access names, "
            f"{len(res.windows)} windows.")

    # --------------------------------------------------------------- filters
    def _filter_tags(self):
        text = self.tag_search.get().strip().lower()
        cat, acc, used = self.tag_cat.get(), self.tag_access.get(), self.tag_used.get()
        hist = self.tag_hist.get()
        rows = [r for r in self.tags_table.rows
                if (cat == ALL or cat in (r[1], r[2]))
                and (hist == ALL or r[8] == hist
                     or (hist == "Logged (any)" and r[8] != "Not logged"))
                and (acc == ALL or r[3].lower() == acc.lower())
                and (used == ALL or r[9] == used)
                and (not text or any(text in str(v).lower() for v in (r[0], r[3], r[4], r[5], r[6])))]
        self.tags_table.show(rows)
        self.tag_count.configure(text=f"{len(rows)} of {len(self.tags_table.rows)} tags")

    def _filter_windows(self):
        text = self.win_search.get().strip().lower()
        self.win_table.show([r for r in self.win_table.rows
                             if not text or text in r[0].lower() or text in r[5].lower()])

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
            export_excel(self.result, path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Export failed", str(exc))
            return
        self.status_var.set(f"Exported {path}")
        messagebox.showinfo("Export", f"Report saved to:\n{path}")

    def _export_csv(self):
        if not self.result:
            return
        folder = filedialog.askdirectory(title="Select folder for CSV files")
        if not folder:
            return
        folder = os.path.join(folder, self._default_name())
        try:
            files = export_csv(self.result, folder)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Export failed", str(exc))
            return
        self.status_var.set(f"Exported {len(files)} CSV files to {folder}")
        messagebox.showinfo("Export", f"{len(files)} CSV files saved to:\n{folder}")


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
