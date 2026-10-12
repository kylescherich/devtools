import contextlib
import io
import unittest

import jsondiff
from jsondiff.cli import main


class TestJsondiffSmoke(unittest.TestCase):
    def test_package_imports_and_cli_stub_returns_usage(self):
        self.assertTrue(jsondiff.__version__)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main([])
        output = err.getvalue()
        self.assertTrue(output)
        self.assertEqual(code, 2)
        self.assertIn("usage: jsondiff", output)
