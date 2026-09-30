"""Engineering classification: mimic types, DI/DO/AI/AO, alarms, history models.

InTouch does not record everything an engineering summary needs (e.g. whether
an I/O point is an input or an output), so part of the classification uses
editable rules (regular expressions) stored in a JSON settings file.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .models import AnalysisResult, Source, Tag

SETTINGS_DIR = os.path.join(os.path.expanduser("~"), ".intouch_analyzer")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")

PROCESS, POPUP, OVERLAY, OTHER = "Process", "Popup", "Overlay", "Other"
MIMIC_TYPES = (PROCESS, POPUP, OVERLAY, OTHER)

DEFAULT_RULES: "OrderedDict[str, object]" = OrderedDict([
    # Window (mimic) type by window name, used when the type is not known
    # from an XML export or a manual override. First match wins.
    ("popup_windows", r"(?i)(popup|pop_|^pu_|_pu$|faceplate|^fp_|_fp$|dialog|dlg|keypad|"
                      r"confirm|trend_?pop)"),
    ("overlay_windows", r"(?i)(overlay|^ov_|banner|header|footer|nav_?bar|navigation|menu|"
                        r"alarm_?bar|toolbar)"),
    ("other_windows", r"(?i)(template|^test|backup|_bak$|_old$|^old_|^copy)"),
    # I/O direction. Tested against "name comment item" of each I/O tag.
    ("pulse_tags", r"(?i)(pulse|_pls\b)"),
    ("output_tags", r"(?i)(_cmd|cmd\b|_ctrl|_sp\b|setpoint|_set\b|_out\b|_op\b|_do\b|"
                    r"_ao\b|_start\b|_stop\b|_reset\b|demand)"),
    ("input_tags", r"(?i)(_pv\b|_fb\b|_sts\b|_status|_di\b|_ai\b|_run\b|_fault|_alarm|_trip)"),
    # Tags with ReadOnly = No that are assigned in a script are outputs.
    ("script_writes_are_outputs", True),
    # Measurement categories for the historisation summary. Tested against
    # "name comment engunits group". First match wins, else "Other".
    ("measurement_categories", [
        ["Flow", r"(?i)(flow|\bf[it]\d|^f[it]_?\d|m3/h|m\^3/h|l/s|l/min|ml/d)"],
        ["Level", r"(?i)(level|\blt\d|^l[it]_?\d|\blvl)"],
        ["Pressure", r"(?i)(press|\bpt\d|^p[it]_?\d|\bbar\b|kpa|mbar|psi)"],
        ["Speed", r"(?i)(speed|\bsic?\d|rpm|\bhz\b|frequency)"],
        ["Temperature", r"(?i)(temp|\btt\d|^t[it]_?\d|deg ?c|°c)"],
        ["Treatment", r"(?i)(dose|dosing|chlor|\bph\b|turbid|treat|\bcl2|ntu)"],
        ["Power", r"(?i)(power|\bkw\b|\bkwh\b|current|amps|\bv\b|volt)"],
    ]),
])


def load_rules(path: str = SETTINGS_FILE) -> Dict[str, object]:
    rules = dict(DEFAULT_RULES)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        rules.update({k: v for k, v in saved.get("rules", {}).items() if k in DEFAULT_RULES})
    except (OSError, ValueError):
        pass
    return rules


def load_overrides(app_path: str, path: str = SETTINGS_FILE) -> Dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return {}
    return dict(saved.get("mimic_overrides", {}).get(os.path.abspath(app_path), {}))


def save_settings(rules: Optional[Dict[str, object]] = None, app_path: str = "",
                  overrides: Optional[Dict[str, str]] = None,
                  path: str = SETTINGS_FILE) -> None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    if rules is not None:
        data["rules"] = {k: v for k, v in rules.items() if DEFAULT_RULES.get(k) != v}
    if app_path and overrides is not None:
        data.setdefault("mimic_overrides", {})[os.path.abspath(app_path)] = overrides
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def validate_rules(rules: Dict[str, object]) -> List[str]:
    """Return a list of error messages for invalid regular expressions."""
    errors = []
    for key, value in rules.items():
        patterns = []
        if isinstance(value, str):
            patterns = [(key, value)]
        elif key == "measurement_categories":
            patterns = [(f"{key}/{c[0]}", c[1]) for c in value]
        for label, pat in patterns:
            try:
                re.compile(pat)
            except re.error as exc:
                errors.append(f"{label}: {exc}")
    return errors


def _search(pattern, text: str) -> bool:
    return bool(pattern) and re.search(pattern, text) is not None


# ---------------------------------------------------------------- mimics
def override_key(src: Source) -> str:
    return f"{src.path}|{src.name}"


def classify_window(src: Source, rules: Dict[str, object],
                    overrides: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
    """Return (mimic type, how it was determined)."""
    if overrides and override_key(src) in overrides:
        return overrides[override_key(src)], "Manual"
    wt = (src.window_type or "").lower()
    if wt:
        if "replace" in wt:
            return PROCESS, "Window type"
        if "overlay" in wt:
            return OVERLAY, "Window type"
        if "popup" in wt or "pop-up" in wt:
            return POPUP, "Window type"
    for key, mtype in (("other_windows", OTHER), ("popup_windows", POPUP),
                       ("overlay_windows", OVERLAY)):
        if _search(rules.get(key), src.name):
            return mtype, "Name rule"
    return PROCESS, "Default"


# ---------------------------------------------------------------- I/O
def io_class(tag: Tag, rules: Dict[str, object], written: bool) -> str:
    """Return 'AI', 'AO', 'DI', 'DO', 'PULSE', 'MSG' or '' for non-I/O tags."""
    if not tag.is_io:
        return ""
    t = tag.tag_type.lower()
    if t == "iomsg":
        return "MSG"
    analog = t in ("ioint", "ioreal")
    text = f"{tag.name} {tag.comment} {tag.item_name}"
    if not analog and _search(rules.get("pulse_tags"), text):
        return "PULSE"
    if _search(rules.get("output_tags"), text):
        output = True
    elif _search(rules.get("input_tags"), text):
        output = False
    elif tag.get("ReadOnly").lower() == "yes":
        output = False
    else:
        output = bool(rules.get("script_writes_are_outputs")) and written
    if analog:
        return "AO" if output else "AI"
    return "DO" if output else "DI"


def alarm_count(tag: Tag) -> int:
    """Number of configured alarm conditions on a tag."""
    n = 0
    state = tag.get("AlarmState").strip().lower()
    if state and state not in ("none", "no"):
        n += 1
    for col in ("LoLoAlarmState", "LoAlarmState", "HiAlarmState", "HiHiAlarmState",
                "MinorDevAlarmState", "MajorDevAlarmState", "ROCAlarmState"):
        if tag.get(col).strip().lower() == "on":
            n += 1
    return n


def measurement_category(tag: Tag, rules: Dict[str, object]) -> str:
    text = f"{tag.name} {tag.comment} {tag.get('EngUnits')} {tag.group}"
    for name, pat in rules.get("measurement_categories", []):
        if _search(pat, text):
            return name
    return "Other"


def _num(v: str) -> Optional[float]:
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def history_model(tag: Tag) -> str:
    """'Change-2.5%', 'Change-0.5' (EU when no span), 'Cyclic 10 s', ..."""
    h = tag.history
    if h.startswith("On change"):
        m = re.search(r"deadband ([\d.]+)", h)
        if not m:
            return "Change-0%"
        db = float(m.group(1))
        lo, hi = _num(tag.get("MinEU")), _num(tag.get("MaxEU"))
        if lo is not None and hi is not None and hi > lo:
            return f"Change-{db / (hi - lo) * 100:.3g}%"
        return f"Change-{db:g}"
    return h


# ---------------------------------------------------------------- summary
@dataclass
class EngineeringSummary:
    mimic_types: Dict[str, Tuple[str, str]]   # override key -> (type, how)
    mimic_counts: Counter
    io_classes: Dict[str, str]                # tag key -> AI/AO/DI/DO/PULSE/MSG
    io_counts: Counter
    alarm_conditions: int
    alarmed_tags: int
    historised: int
    history_models: List[Tuple[str, str, int]]  # (category, model, count)
    plc_connections: List[str]

    def rows(self, total_mimics: int, total_io: int) -> List[Tuple[str, object]]:
        """The analysis report, in the standard format. Counts are ints."""
        c, io = self.mimic_counts, self.io_counts
        analog = io["AI"] + io["AO"]
        digital = io["DI"] + io["DO"] + io["PULSE"]
        return [
            ("Number of mimics", total_mimics),
            ("Mimic Breakdown by type (Process, Popups)",
             f"Process - {c[PROCESS]} / Popups (On Top + Overlay) - {c[POPUP] + c[OVERLAY]}"),
            ("Number of data points (I/O tags)", total_io),
            ("Breakdown by type (DI / DO / AI / AO)",
             f"Analogues - {analog} / Digital - {digital}"),
            ("   of which (DI / DO / AI / AO)",
             f"AI {io['AI']} / AO {io['AO']} / DI {io['DI']} / DO {io['DO']} / PULSE {io['PULSE']}"),
            ("Number of configured alarms", self.alarm_conditions),
            ("Number of historised points", self.historised),
            ("Historisation rate", "\n".join(self.history_model_lines()) or "None"),
            ("Number of PLC connections", len(self.plc_connections)),
        ]

    def detail_rows(self) -> List[Tuple[str, object]]:
        """Supporting breakdowns that are not part of the standard report."""
        c, io = self.mimic_counts, self.io_counts
        return [
            ("Process mimics", c[PROCESS]),
            ("Popup (On Top) mimics", c[POPUP]),
            ("Overlay mimics", c[OVERLAY]),
            ("Other mimics (not counted in the breakdown)", c[OTHER]),
            ("Message I/O tags (not counted as analogue/digital)", io["MSG"]),
            ("Tags with alarms", self.alarmed_tags),
            ("PLC connections", ", ".join(self.plc_connections) or "None"),
        ]

    def history_model_lines(self) -> List[str]:
        return [f"Model {i}-{cat}-{model}-{n}"
                for i, (cat, model, n) in enumerate(self.history_models, start=1)]


def engineering_summary(r: AnalysisResult, rules: Optional[Dict[str, object]] = None,
                        overrides: Optional[Dict[str, str]] = None) -> EngineeringSummary:
    rules = rules if rules is not None else load_rules()
    mimic_types = {override_key(w): classify_window(w, rules, overrides) for w in r.windows}
    written = set()
    for s in r.sources:
        written.update(s.tag_writes)
    io_classes = {t.key: io_class(t, rules, t.key in written) for t in r.io_tags}

    alarm_conditions = alarmed = 0
    models: Counter = Counter()
    for t in r.tags.values():
        n = alarm_count(t)
        alarm_conditions += n
        alarmed += bool(n)
        if t.is_logged:
            models[(measurement_category(t, rules), history_model(t))] += 1

    used_access = {t.access_name.lower() for t in r.io_tags if t.access_name}
    connections = {}
    for key in sorted(used_access):
        acc = r.access_names.get(key)
        if acc is None and r.access_names:
            continue  # undefined access name: reported under Issues, not a connection
        # Several access names can point at the same PLC (application|topic).
        conn = f"{acc.application}|{acc.topic}".lower() if acc else key
        connections.setdefault(conn, acc.name if acc else key)

    return EngineeringSummary(
        mimic_types=mimic_types,
        mimic_counts=Counter(t for t, _ in mimic_types.values()),
        io_classes=io_classes,
        io_counts=Counter(io_classes.values()),
        alarm_conditions=alarm_conditions,
        alarmed_tags=alarmed,
        historised=sum(models.values()),
        history_models=sorted(((c, m, n) for (c, m), n in models.items()),
                              key=lambda x: (x[0] == "Other", x[0], x[1])),
        plc_connections=sorted(connections.values(), key=str.lower),
    )
