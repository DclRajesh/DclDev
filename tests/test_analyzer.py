import os
import struct
import tempfile
import unittest

from intouch_analyzer.analyzer import analyze
from intouch_analyzer.export import build_tables, export_csv

DBDUMP = """:mode=ask
:IOAccess,Application,Topic,AdviseActive,DDEProtocol,SecApplication,SecTopic
"PLC1","DASABCIP","Line1",No,No,"",""
"PLC2","DASMBTCP","Line2",Yes,No,"",""
"SPARE","DASSIDirect","Spare",No,No,"",""
:IODisc,Group,Comment,Logged,EventLogged,RetentiveValue,InitialDisc,AlarmState,AccessName,ItemUseTagname,ItemName
"Pump1_Run","$System","Pump 1 running",Yes,No,No,Off,On,"PLC1",No,"N7:0/1"
"Pump2_Run","$System","Pump 2 running",No,No,No,Off,None,"PLC1",No,"N7:0/1"
"Valve-3","$System","Valve 3 open",No,No,No,Off,None,"PLC2",Yes,""
"Orphan","$System","",No,No,No,Off,None,"NOPE",No,"X1"
:IOReal,Group,Comment,Logged,MinEU,MaxEU,HiAlarmState,AccessName,ItemUseTagname,ItemName
"Tank1_Level","$System","Tank level",No,0,100,On,"PLC2",No,"40001"
:MemoryInt,Group,Comment,Logged,InitialValue
"ScreenMode","$System","Current screen",No,0
"UnusedMem","$System","",No,0
"""


def _binary_window(strings_ascii, strings_utf16):
    blob = bytearray(b"\x00\x01WIN\x02" + struct.pack("<I", 1234))
    for s in strings_ascii:
        blob += b"\x00\x07" + s.encode("ascii") + b"\x00\xff"
    for s in strings_utf16:
        blob += b"\x00\x00\x09" + s.encode("utf-16-le") + b"\x00\x00\xfe"
    return bytes(blob)


XML_WINDOW = """<?xml version="1.0" encoding="utf-8"?>
<Windows>
  <Window Name="Overview">
    <Object Type="Rectangle">
      <Animation Type="FillColorDiscrete" Expression="Pump1_Run AND Pump2_Run"/>
    </Object>
    <Script>IF Tank1_Level.Value &gt; 90 THEN Show "Alarms"; ENDIF;
      Missing_Tag.Value = 1;</Script>
  </Window>
  <Window Name="Alarms">
    <Object Expression="PLC1:&quot;N7:0/5&quot; + ScreenMode"/>
  </Window>
</Windows>
"""


def make_app(root, with_dbdump=True):
    os.makedirs(root, exist_ok=True)
    if with_dbdump:
        with open(os.path.join(root, "tags.csv"), "w", encoding="utf-8") as fh:
            fh.write(DBDUMP)
    with open(os.path.join(root, "win00001.win"), "wb") as fh:
        fh.write(_binary_window(["Pump1_Run", "Tank1_Level.MaxEU > 50"],
                                ["Valve-3 == 1", 'ShowTopWindow("Overview")']))
    with open(os.path.join(root, "win00002.win"), "wb") as fh:
        fh.write(_binary_window(["Arial", "Nothing here"], []))
    os.makedirs(os.path.join(root, "exports"))
    with open(os.path.join(root, "exports", "windows.xml"), "w", encoding="utf-8") as fh:
        fh.write(XML_WINDOW)
    with open(os.path.join(root, "appscr.scr"), "wb") as fh:
        fh.write(_binary_window(["ScreenMode = 2;"], []))
    with open(os.path.join(root, "history.lgh"), "wb") as fh:
        fh.write(b"Pump2_Run" * 10)


class AnalyzerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = os.path.join(self.tmp.name, "MyApp")
        make_app(self.app)
        self.res = analyze(self.app)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tag_database(self):
        r = self.res
        self.assertEqual(len(r.tags), 7)
        self.assertEqual(len(r.io_tags), 5)
        self.assertEqual(set(r.access_names), {"plc1", "plc2", "spare"})
        v = r.find_tag("valve-3")
        self.assertEqual(v.item_name, "Valve-3")  # ItemUseTagname
        self.assertTrue(r.find_tag("Pump1_Run").has_alarm)
        self.assertTrue(r.find_tag("Tank1_Level").has_alarm)
        self.assertFalse(r.find_tag("Pump2_Run").has_alarm)
        self.assertTrue(r.find_tag("Pump1_Run").is_logged)

    def test_windows(self):
        names = {w.name: w for w in self.res.windows}
        self.assertEqual(set(names), {"win00001", "win00002", "Overview", "Alarms"})
        w1 = names["win00001"]
        self.assertEqual(set(w1.tag_refs), {"pump1_run", "tank1_level", "valve-3"})
        self.assertEqual(w1.opens_windows, {"Overview"})
        ov = names["Overview"]
        self.assertEqual(set(ov.tag_refs), {"pump1_run", "pump2_run", "tank1_level"})
        self.assertIn("Alarms", ov.opens_windows)
        self.assertIn("Missing_Tag", ov.unresolved)
        al = names["Alarms"]
        self.assertEqual(set(al.tag_refs), {"screenmode"})
        self.assertIn("PLC1:N7:0/5", al.remote_refs)
        self.assertFalse(names["win00002"].tag_refs)

    def test_other_files(self):
        others = {s.name for s in self.res.other_sources}
        self.assertEqual(others, {"appscr.scr"})  # .lgh skipped

    def test_usage_and_issues(self):
        r = self.res
        self.assertEqual({t.name for t in r.unused_tags()}, {"Orphan", "UnusedMem"})
        dups = r.duplicate_io_addresses()
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0][0], "PLC1:N7:0/1")
        cats = {(i.category, i.item) for i in r.issues()}
        self.assertIn(("Undefined access name", "Orphan"), cats)
        self.assertIn(("Access name has no tags", "SPARE"), cats)
        self.assertIn(("Reference to undefined tag", "Missing_Tag"), cats)
        self.assertIn(("Window references no tags", "win00002"), cats)
        from intouch_analyzer.classify import DEFAULT_RULES, engineering_summary
        eng = engineering_summary(r, dict(DEFAULT_RULES), {})
        self.assertEqual(eng.plc_connections, ["PLC1", "PLC2"])  # "NOPE" is undefined

    def test_tables_and_csv_export(self):
        tables = build_tables(self.res)
        headers, rows = tables["Tags"]
        pump1 = next(r for r in rows if r[0] == "Pump1_Run")
        self.assertEqual(pump1[headers.index("# Windows")], 2)
        out = os.path.join(self.tmp.name, "csv")
        files = export_csv(self.res, out)
        self.assertEqual(len(files), len(tables))
        self.assertTrue(all(os.path.getsize(f) > 0 for f in files))

    def test_excel_export(self):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self.skipTest("openpyxl not installed")
        from intouch_analyzer.export import export_excel
        path = os.path.join(self.tmp.name, "out.xlsx")
        export_excel(self.res, path)
        wb = openpyxl.load_workbook(path)
        self.assertIn("Window-Tag Map", wb.sheetnames)


class FallbackTest(unittest.TestCase):
    def test_tagname_x_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_app(tmp, with_dbdump=False)
            with open(os.path.join(tmp, "tagname.x"), "wb") as fh:
                fh.write(_binary_window(["Pump1_Run", "Tank1_Level", "$System"], ["Valve-3"]))
            res = analyze(tmp)
            self.assertLessEqual({"pump1_run", "tank1_level", "valve-3"}, set(res.tags))
            self.assertNotIn("$system", res.tags)
            self.assertTrue(any("heuristically" in w for w in res.warnings))
            w1 = next(w for w in res.windows if w.name == "win00001")
            self.assertIn("pump1_run", w1.tag_refs)

    def test_utf16_dbdump(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "dump.csv"), "wb") as fh:
                fh.write(b"\xff\xfe" + DBDUMP.encode("utf-16-le"))
            res = analyze(tmp)
            self.assertEqual(len(res.tags), 7)

    def test_missing_folder(self):
        with self.assertRaises(FileNotFoundError):
            analyze("/definitely/not/here")


if __name__ == "__main__":
    unittest.main()
