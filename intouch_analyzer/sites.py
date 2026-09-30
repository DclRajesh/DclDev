"""HMI site survey: find a site's InTouch application, copy it locally, report.

Site folders live under the "04 Sites" folder of the HMI Site Survey
SharePoint library (synced with OneDrive). For a request such as
"Baunton survey report" the site folder is found by name, the InTouch
application inside it is located (extracting zip backups when there is no
unpacked application), copied to a local InTouch working folder and analysed,
and the report is written next to the copy.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .classify import load_folders
from .dbdump import looks_like_dbdump

ProgressFn = Callable[[str], None]

_SITES_SUFFIX = os.path.join("System Platform - Documents", "HMI Site Survey", "04 Sites")
SITES_ROOT_CANDIDATES = [
    os.path.join(r"C:\Users\DeepthiDonavalli", "Donavalli consulting Limited(1)", _SITES_SUFFIX),
    os.path.join(os.path.expanduser("~"), "Donavalli consulting Limited(1)", _SITES_SUFFIX),
    os.path.join(os.path.expanduser("~"), "Donavalli consulting Limited", _SITES_SUFFIX),
]
DEFAULT_WORK_ROOT = r"C:\InTouch" if sys.platform == "win32" else \
    os.path.join(os.path.expanduser("~"), "InTouch")

COPY_MARKER = ".intouch_analyzer_copy"  # marks folders this tool created
_REQUEST_WORDS = re.compile(
    r"(?i)\b(hmi|site|survey|report|analysis|analyse|analyze|intouch|for|the|please|run)\b")
_SKIP_DIRS = {"__pycache__", ".git"}


def default_sites_root() -> str:
    saved = load_folders().get("sites_root")
    if saved:
        return saved
    return next((p for p in SITES_ROOT_CANDIDATES if os.path.isdir(p)), SITES_ROOT_CANDIDATES[1])


def default_work_root() -> str:
    return load_folders().get("work_root") or DEFAULT_WORK_ROOT


def site_name_from_request(request: str) -> str:
    """'Baunton survey report' -> 'Baunton'."""
    name = _REQUEST_WORDS.sub(" ", request)
    return re.sub(r"\s+", " ", name).strip(" -_,.") or request.strip()


def list_sites(sites_root: str) -> List[str]:
    try:
        return sorted((d for d in os.listdir(sites_root)
                       if os.path.isdir(os.path.join(sites_root, d)) and not d.startswith(".")),
                      key=str.lower)
    except OSError:
        return []


def find_sites(sites_root: str, request: str) -> List[str]:
    """Site folder names matching a request: exact, then prefix, then contains."""
    name = site_name_from_request(request).lower()
    sites = list_sites(sites_root)
    for test in (lambda s: s.lower() == name,
                 lambda s: s.lower().startswith(name),
                 lambda s: name in s.lower(),
                 lambda s: all(w in s.lower() for w in name.split())):
        found = [s for s in sites if test(s)]
        if found:
            return found
    return []


def is_intouch_app(folder: str) -> bool:
    try:
        names = [n.lower() for n in os.listdir(folder)]
    except OSError:
        return False
    return "tagname.x" in names or any(n.endswith(".win") for n in names)


def find_intouch_apps(folder: str) -> List[str]:
    """InTouch application folders (containing tagname.x or *.win) below folder."""
    apps = []
    for dirpath, dirnames, _ in os.walk(folder):
        if is_intouch_app(dirpath):
            apps.append(dirpath)
            dirnames[:] = []  # don't descend into an application
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
    return apps


def find_zips(folder: str) -> List[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        out += [os.path.join(dirpath, f) for f in sorted(filenames) if f.lower().endswith(".zip")]
    return out


def find_tag_dumps(folder: str, exclude: List[str] = ()) -> List[str]:
    """DBDump CSVs anywhere in the site folder (outside the application folders)."""
    excl = [os.path.abspath(e) + os.sep for e in exclude]
    out = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if any((os.path.abspath(dirpath) + os.sep).startswith(e) for e in excl):
            continue
        for f in filenames:
            p = os.path.join(dirpath, f)
            if f.lower().endswith(".csv") and os.path.getsize(p) < 50 * 1024 * 1024 \
                    and looks_like_dbdump(p):
                out.append(p)
    return out


def app_label(app_path: str, site_path: str) -> str:
    rel = os.path.relpath(app_path, site_path)
    return os.path.basename(site_path) if rel == "." else rel


def safe_extract(zip_path: str, dest: str) -> None:
    """Extract a zip, refusing entries that would land outside dest."""
    root = os.path.abspath(dest)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = os.path.abspath(os.path.join(root, member.filename))
            if target != root and not target.startswith(root + os.sep):
                raise ValueError(f"Unsafe path in {os.path.basename(zip_path)}: {member.filename}")
        zf.extractall(root)


def _fresh_dir(path: str) -> None:
    """Empty a folder this tool created earlier; never delete anything else."""
    if os.path.isdir(path):
        if not os.path.exists(os.path.join(path, COPY_MARKER)):
            raise FileExistsError(
                f"{path} already exists and was not created by this tool. "
                "Rename or remove it, or choose a different InTouch working folder.")
        shutil.rmtree(path)
    os.makedirs(path)
    open(os.path.join(path, COPY_MARKER), "w").close()


def _safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "app"


@dataclass
class SiteApps:
    site: str
    site_path: str
    work_dir: str                   # <work root>/<site>
    apps: List[str] = field(default_factory=list)   # InTouch apps found
    extracted: bool = False         # apps come from zip backups (already local)
    tag_dumps: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def locate_site_apps(sites_root: str, site: str, work_root: str,
                     progress: Optional[ProgressFn] = None) -> SiteApps:
    """Find the InTouch application(s) in a site folder."""
    say = progress or (lambda m: None)
    site_path = os.path.join(sites_root, site)
    if not os.path.isdir(site_path):
        raise FileNotFoundError(f"Site folder not found: {site_path}")
    res = SiteApps(site=site, site_path=site_path,
                   work_dir=os.path.join(work_root, _safe_name(site)))
    say(f"Searching {site} for InTouch applications...")
    res.apps = find_intouch_apps(site_path)
    if not res.apps:
        zips = find_zips(site_path)
        if zips:
            extract_root = os.path.join(res.work_dir, "_extracted")
            _fresh_dir(extract_root)
            for z in zips:
                say(f"Extracting {os.path.basename(z)}...")
                try:
                    safe_extract(z, os.path.join(extract_root,
                                                 _safe_name(os.path.splitext(os.path.basename(z))[0])))
                except (zipfile.BadZipFile, ValueError, OSError) as exc:
                    res.notes.append(f"Could not extract {os.path.basename(z)}: {exc}")
            res.apps = find_intouch_apps(extract_root)
            res.extracted = True
    res.tag_dumps = find_tag_dumps(site_path, exclude=[] if res.extracted else res.apps)
    if not res.apps:
        raise FileNotFoundError(
            f"No InTouch application (a folder with tagname.x or *.win files, or a zip "
            f"backup of one) was found in {site_path}")
    # Most likely the main application first: most windows.
    res.apps.sort(key=lambda a: -sum(f.lower().endswith(".win") for f in os.listdir(a)))
    return res


def copy_app(site_apps: SiteApps, app_path: str,
             progress: Optional[ProgressFn] = None) -> str:
    """Copy an application (and any DBDump CSVs from the site folder) locally.

    Returns the local application folder. A previous copy made by this tool
    is replaced; any other existing folder is left untouched (error)."""
    say = progress or (lambda m: None)
    if site_apps.extracted:
        local = app_path  # already extracted into the working folder
    else:
        local = os.path.join(site_apps.work_dir,
                             _safe_name(os.path.basename(app_path.rstrip("\\/"))))
        say(f"Copying {app_label(app_path, site_apps.site_path)} to {local}...")
        _fresh_dir(local)
        shutil.copytree(app_path, local, dirs_exist_ok=True)
    has_own_dump = any(f.lower().endswith(".csv") and looks_like_dbdump(os.path.join(local, f))
                       for f in os.listdir(local))
    for i, dump in enumerate([] if has_own_dump else site_apps.tag_dumps):
        name = os.path.basename(dump)
        dest = os.path.join(local, name if not os.path.exists(os.path.join(local, name))
                            else f"site_tagdump_{i}_{name}")
        shutil.copy2(dump, dest)
    return local


def report_path(site_apps: SiteApps) -> str:
    return os.path.join(site_apps.work_dir, f"{_safe_name(site_apps.site)} HMI Survey Report.xlsx")
