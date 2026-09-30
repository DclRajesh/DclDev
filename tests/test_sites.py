import contextlib
import io
import os
import tempfile
import unittest
import zipfile

from intouch_analyzer import cli
from intouch_analyzer.sites import (COPY_MARKER, copy_app, find_sites, locate_site_apps,
                                    report_path, safe_extract, site_name_from_request)

from test_analyzer import DBDUMP, _binary_window


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as fh:
        fh.write(data)


def make_sites(root):
    # Baunton: application nested in a backup folder, DBDump elsewhere in the site
    app = os.path.join(root, "Baunton", "HMI Backup", "InTouch", "BauntonApp")
    _write(os.path.join(app, "tagname.x"), b"\x00")
    _write(os.path.join(app, "win00001.win"), _binary_window(["Pump1_Run", "Valve-3"], []))
    _write(os.path.join(app, "win00002.win"), _binary_window(["Tank1_Level"], []))
    _write(os.path.join(root, "Baunton", "Survey", "tags dump.csv"), DBDUMP)
    _write(os.path.join(root, "Baunton", "Survey", "notes.docx"), b"x")
    # a second, smaller application at the same site
    _write(os.path.join(root, "Baunton", "Old", "Test", "win00001.win"), b"\x00")
    _write(os.path.join(root, "Baunton North", "readme.txt"), "x")
    # Cheltenham: only a zip backup
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ChelApp/tagname.x", b"\x00")
        zf.writestr("ChelApp/win00001.win", _binary_window(["Pump1_Run"], []))
        zf.writestr("ChelApp/dump.csv", DBDUMP)
    _write(os.path.join(root, "Cheltenham WTW", "Backups", "hmi.zip"), buf.getvalue())
    os.makedirs(os.path.join(root, "Empty Site"))


class SitesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "04 Sites")
        self.work = os.path.join(self.tmp.name, "InTouch")
        make_sites(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_request_parsing(self):
        self.assertEqual(site_name_from_request("Baunton survey report"), "Baunton")
        self.assertEqual(site_name_from_request("HMI site survey for Cheltenham WTW"),
                         "Cheltenham WTW")
        self.assertEqual(find_sites(self.root, "Baunton survey report"), ["Baunton"])
        self.assertEqual(find_sites(self.root, "baunton n"), ["Baunton North"])
        self.assertEqual(find_sites(self.root, "cheltenham"), ["Cheltenham WTW"])
        self.assertEqual(find_sites(self.root, "Nowhere"), [])

    def test_locate_and_copy(self):
        found = locate_site_apps(self.root, "Baunton", self.work)
        self.assertEqual([os.path.basename(a) for a in found.apps], ["BauntonApp", "Test"])
        self.assertEqual([os.path.basename(d) for d in found.tag_dumps], ["tags dump.csv"])
        local = copy_app(found, found.apps[0])
        self.assertEqual(local, os.path.join(self.work, "Baunton", "BauntonApp"))
        self.assertTrue(os.path.isfile(os.path.join(local, "win00002.win")))
        self.assertTrue(os.path.isfile(os.path.join(local, "tags dump.csv")))
        # the source is untouched
        self.assertFalse(os.path.exists(os.path.join(found.apps[0], COPY_MARKER)))
        # re-running replaces the previous copy (stale files go)
        _write(os.path.join(local, "stale.win"), b"x")
        copy_app(found, found.apps[0])
        self.assertFalse(os.path.exists(os.path.join(local, "stale.win")))
        self.assertEqual(report_path(found),
                         os.path.join(self.work, "Baunton", "Baunton HMI Survey Report.xlsx"))

    def test_existing_folder_not_created_by_tool_is_kept(self):
        found = locate_site_apps(self.root, "Baunton", self.work)
        mine = os.path.join(self.work, "Baunton", "BauntonApp")
        _write(os.path.join(mine, "important.txt"), "keep me")
        with self.assertRaises(FileExistsError):
            copy_app(found, found.apps[0])
        self.assertTrue(os.path.isfile(os.path.join(mine, "important.txt")))

    def test_zip_backup(self):
        found = locate_site_apps(self.root, "Cheltenham WTW", self.work)
        self.assertTrue(found.extracted)
        self.assertEqual([os.path.basename(a) for a in found.apps], ["ChelApp"])
        local = copy_app(found, found.apps[0])
        self.assertTrue(local.startswith(os.path.join(self.work, "Cheltenham WTW")))

    def test_no_application(self):
        with self.assertRaises(FileNotFoundError):
            locate_site_apps(self.root, "Empty Site", self.work)

    def test_zip_path_traversal_refused(self):
        z = os.path.join(self.tmp.name, "evil.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("../../escaped.txt", "x")
        with self.assertRaises(ValueError):
            safe_extract(z, os.path.join(self.tmp.name, "out"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "escaped.txt")))

    def test_cli_site_survey(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cli.main(["--site", "Baunton survey report", "--sites-root", self.root,
                           "--work-dir", self.work])
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("Baunton HMI survey report", text)
        self.assertIn("Number of mimics", text)
        self.assertRegex(text, r"Number of data points \(I/O tags\)\s+5")
        report = os.path.join(self.work, "Baunton", "Baunton HMI Survey Report.xlsx")
        try:
            import openpyxl  # noqa: F401
            self.assertTrue(os.path.isfile(report))
        except ImportError:
            self.assertTrue(os.path.isdir(os.path.splitext(report)[0]))


if __name__ == "__main__":
    unittest.main()
