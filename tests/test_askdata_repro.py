"""Two runs of the same report are byte-identical (BRIEF-DEVWS-017 item 6)."""
import contextlib, io, tempfile, unittest
from pathlib import Path
from askdata.cli import main
from tests.askdata_fixtures import clean_csv, fk_db

def _run(args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(args)
    return code, out.getvalue()

class TestReproducibleReports(unittest.TestCase):
    def test_reports_byte_identical(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as o1, tempfile.TemporaryDirectory() as o2:
            for path in (clean_csv(src), fk_db(src)):
                for extra in ([], ["--samples"]):
                    c1, t1 = _run(["inspect", str(path)] + extra)
                    c2, t2 = _run(["inspect", str(path)] + extra)
                    self.assertEqual((c1, c2), (0, 0))
                    self.assertTrue(t1.strip())
                    self.assertEqual(t1, t2)
                    for ext in ("html", "md"):
                        name = f"{path.stem}{'-s' if extra else ''}.{ext}"
                        for d in (o1, o2):
                            self.assertEqual(_run(["inspect", str(path), "--output", str(Path(d) / name)] + extra)[0], 0)
                        b1, b2 = (Path(o1) / name).read_bytes(), (Path(o2) / name).read_bytes()
                        self.assertTrue(b1)
                        self.assertEqual(b1, b2)

if __name__ == "__main__":
    unittest.main()
