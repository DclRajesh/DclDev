import os
import tempfile
import unittest

from intouch_analyzer.analyzer import analyze
from intouch_analyzer.export import build_tables, history_rows, summary_rows
from intouch_analyzer.history import describe_history, parse_history_file

DBDUMP = """:mode=ask
:IOAccess,Application,Topic
"PLC1","DASABCIP","Line1"
:IODisc,Group,Comment,Logged,AccessName,ItemName
"Run1","$System","",Yes,"PLC1","B3:0/0"
"Run2","$System","",Yes,"PLC1","B3:0/1"
"Run3","$System","",No,"PLC1","B3:0/2"
:IOReal,Group,Comment,Logged,LogDeadband,AccessName,ItemName
"Flow1","$System","",Yes,0.5,"PLC1","F8:0"
"Flow2","$System","",Yes,0,"PLC1","F8:1"
"Level1","$System","",No,0,"PLC1","F8:2"
:MemoryReal,Group,Comment,Logged,LogDeadband
"Calc1","$System","",Yes,0
"""

# Historian configuration-export style (section header, StorageRate in ms)
HISTORIAN = """:(AnalogTag)TagName,Description,StorageType,StorageRate,ValueDeadband
"Flow1","",Cyclic,10000,0
"Level1","",Cyclic,10000,0
"Flow2","",Cyclic,60000,0
"Other.Unknown","",Cyclic,1000,0
:(DiscreteTag)TagName,Description,StorageType,StorageRate
"Run1","",Delta,0
"MyNode.Run3","",Forced,0
"""


class DescribeHistoryTest(unittest.TestCase):
    def test_intouch_logging(self):
        self.assertEqual(describe_history({"Logged": "Yes"}), "On change")
        self.assertEqual(describe_history({"Logged": "Yes", "LogDeadband": "0.5"}),
                         "On change (deadband 0.5)")
        self.assertEqual(describe_history({"Logged": "No"}), "Not logged")
        self.assertEqual(describe_history({"Comment": "x"}), "")

    def test_storage_columns(self):
        self.assertEqual(describe_history({"StorageType": "Cyclic", "StorageRate": "10000"},
                                          rate_unit="ms"), "Cyclic 10 s")
        self.assertEqual(describe_history({"StorageRate": "60"}), "Cyclic 1 min")
        self.assertEqual(describe_history({"LogInterval": "00:00:10"}), "Cyclic 10 s")
        self.assertEqual(describe_history({"StorageRateMs": "500"}), "Cyclic 500 ms")
        self.assertEqual(describe_history({"StorageType": "Delta", "ValueDeadband": "2"}),
                         "On change (deadband 2)")
        self.assertEqual(describe_history({"StorageType": "Forced"}), "Forced (every change)")
        self.assertEqual(describe_history({"StorageType": "None", "Logged": "Yes"}),
                         "Not logged")


class HistoryAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = self.tmp.name
        with open(os.path.join(self.app, "dump.csv"), "w") as fh:
            fh.write(DBDUMP)
        self.hist = os.path.join(self.app, "historian_tags.txt")
        with open(self.hist, "w") as fh:
            fh.write(HISTORIAN)

    def tearDown(self):
        self.tmp.cleanup()

    def test_dbdump_only(self):
        res = analyze(self.app)
        rows = {d: (n, detail) for d, n, detail in history_rows(res)}
        self.assertEqual(rows["On change"], (4, "I/O 3, Memory 1"))
        self.assertEqual(rows["On change (deadband 0.5)"], (1, "I/O 1"))
        self.assertNotIn("Not logged", rows)
        summary = dict(summary_rows(res))
        self.assertEqual(summary["Historised (logged) tags"], "5")
        self.assertEqual(summary["  History: On change"], "4 tags (I/O 3, Memory 1)")

    def test_historian_file_overrides(self):
        res = analyze(self.app, history_paths=[self.hist])
        t = res.tags
        self.assertEqual(t["flow1"].history, "Cyclic 10 s")
        self.assertEqual(t["level1"].history, "Cyclic 10 s")
        self.assertEqual(t["flow2"].history, "Cyclic 1 min")
        self.assertEqual(t["run1"].history, "On change")
        self.assertEqual(t["run3"].history, "Forced (every change)")  # node prefix stripped
        self.assertEqual(t["run2"].history, "On change")  # from DBDump
        order = [d for d, _, _ in history_rows(res)]
        self.assertEqual(order, ["Cyclic 10 s", "Cyclic 1 min", "Forced (every change)",
                                 "On change"])
        self.assertTrue(any("1 of 6 historised tags" in w for w in res.warnings))
        self.assertFalse(res.sources)  # the Historian file is not scanned as a script
        headers, rows = build_tables(res)["History Rates"]
        self.assertEqual(headers, ["History / Storage Rate", "Tags", "I/O", "Memory"])
        self.assertEqual(rows[0], ("Cyclic 10 s", 2, 2, 0))

    def test_plain_csv_and_tab_delimited(self):
        plain = os.path.join(self.app, "h.csv")
        with open(plain, "w") as fh:
            fh.write("TagName,StorageType,StorageRate\nFlow1,Cyclic,5000\n")
        self.assertEqual(parse_history_file(plain), {"flow1": "Cyclic 5 s"})
        tab = os.path.join(self.app, "h.txt")
        with open(tab, "w") as fh:
            fh.write("TagName\tStorageType\tStorageRate\nFlow1\tDelta\t0\n")
        self.assertEqual(parse_history_file(tab), {"flow1": "On change"})


if __name__ == "__main__":
    unittest.main()
