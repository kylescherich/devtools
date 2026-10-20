"""Synthetic repos for the repo-xray --cycles / --boundaries tests.

Each fixture is a dict {relative path: source}; write_repo() builds it in a
temp dir at test time.
"""

from pathlib import Path


SENTINEL = "XRAY_SENTINEL_7f3a91"

# pkg.alpha -> pkg.beta -> pkg.alpha (imports on line 2, after a docstring)
CYCLE2 = {
    "pkg/__init__.py": "",
    "pkg/alpha.py": '"""Alpha."""\nfrom pkg import beta\n',
    "pkg/beta.py": '"""Beta."""\nimport pkg.alpha\n',
}

# written so a naive walk would start at pkg.b or pkg.c
CYCLE3 = {
    "pkg/__init__.py": "",
    "pkg/c.py": "import pkg.a\n",
    "pkg/b.py": "import pkg.c\n",
    "pkg/a.py": "import pkg.b\n",
}

# one 2-cycle (pkg.x, pkg.y) and one 3-cycle (pkg.a, pkg.b, pkg.c)
TWO_CYCLES = dict(CYCLE3, **{
    "pkg/y.py": "import pkg.x\n",
    "pkg/x.py": "import pkg.y\n",
})

# a -> b, b -> a, b -> c, c -> a: the loop through a is a -> b -> a
SHORTEST = {
    "pkg/__init__.py": "",
    "pkg/a.py": "import pkg.b\n",
    "pkg/b.py": "import pkg.a\nimport pkg.c\n",
    "pkg/c.py": "import pkg.a\n",
}

# relative forms: from . import x, from .x import y, from .. import x, from .sub import x
RELATIVE = {
    "pkg/__init__.py": "",
    "pkg/top.py": "from .sub import one\n",
    "pkg/sub/__init__.py": "",
    "pkg/sub/one.py": "from . import two\n",
    "pkg/sub/two.py": "from .three import thing\n",
    "pkg/sub/three.py": "from .. import top\nthing = 1\n",
}

# from pkg import mod -> pkg.mod (a module); from pkg import helper -> pkg
FROM_NAME = {
    "pkg/__init__.py": "from pkg import mod\n\n\ndef helper():\n    return mod\n",
    "pkg/mod.py": "from pkg import helper\n",
}

# pkg.b -> pkg.a and pkg.b -> pkg.c only under TYPE_CHECKING; pkg.d <-> pkg.e is real
TYPE_CHECKING = {
    "pkg/__init__.py": "",
    "pkg/a.py": "import pkg.b\n",
    "pkg/b.py": ("import typing\n"
                 "from typing import TYPE_CHECKING\n"
                 "if TYPE_CHECKING:\n"
                 "    import pkg.a\n"
                 "if typing.TYPE_CHECKING:\n"
                 "    from pkg import c\n"),
    "pkg/c.py": "import pkg.b\n",
    "pkg/d.py": "import pkg.e\n",
    "pkg/e.py": "import pkg.d\n",
}

FUNCTION_LEVEL = {
    "pkg/__init__.py": "",
    "pkg/a.py": "def f():\n    import pkg.b\n    return pkg.b\n",
    "pkg/b.py": "import pkg.a\n",
}

TRY_IMPORT = {
    "pkg/__init__.py": "",
    "pkg/a.py": "try:\n    from pkg import b\nexcept ImportError:\n    b = None\n",
    "pkg/b.py": "import pkg.a\n",
}

# top-level string.py shadows the stdlib name; only a <-> b is a cycle
STDLIB_NAME = {
    "string.py": "import a\n",
    "a.py": "import string\nimport b\n",
    "b.py": "import a\n",
}

NO_CYCLES = {
    "pkg/__init__.py": "",
    "pkg/a.py": "import os\nimport pkg.b\n",
    "pkg/b.py": "import json\nfrom pkg import c\n",
    "pkg/c.py": "",
}

# pkg_a.core imports pkg_b.api on line 2
VIOLATION = {
    "pkg_a/__init__.py": "",
    "pkg_a/core.py": "import os\nfrom pkg_b import api\n",
    "pkg_b/__init__.py": "",
    "pkg_b/api.py": "X = 1\n",
}

# no import between pkg_a and pkg_b
CLEAN = {
    "pkg_a/__init__.py": "",
    "pkg_a/core.py": "import os\n",
    "pkg_b/__init__.py": "",
    "pkg_b/api.py": "X = 1\n",
}

# app is not listed: it imports both listed packages, and pkg_b imports app
UNLISTED = {
    "pkg_a/__init__.py": "",
    "pkg_a/core.py": "X = 1\n",
    "pkg_b/__init__.py": "",
    "pkg_b/api.py": "def run():\n    import app.main\n    return app.main\n",
    "app/__init__.py": "",
    "app/main.py": "import pkg_a.core\nfrom pkg_b import api\n",
}

SELF_IMPORT = {
    "pkg_a/__init__.py": "from pkg_a import core\n",
    "pkg_a/core.py": "from pkg_a import util\nfrom . import util as u2\n",
    "pkg_a/util.py": "X = 1\n",
    "pkg_b/__init__.py": "",
}

TYPE_CHECKING_RULE = {
    "pkg_a/__init__.py": "",
    "pkg_a/core.py": ("from typing import TYPE_CHECKING\n"
                      "if TYPE_CHECKING:\n"
                      "    from pkg_b import api\n"),
    "pkg_b/__init__.py": "",
    "pkg_b/api.py": "X = 1\n",
}

PYPROJECT_OK = '[tool.repo-xray]\nindependent = ["pkg_a", "pkg_b"]\n'
PYPROJECT_OTHER = '[tool.repo-xray]\nindependent = ["pkg_a", "app"]\n'
PYPROJECT_MALFORMED = f'[tool.repo-xray\nindependent = ["{SENTINEL}"]\n'
PYPROJECT_BAD_VALUE = f'[tool.repo-xray]\nindependent = "{SENTINEL}"\n'


def write_repo(base, files):
    """Write files {relative path: source} under base; return base as Path."""
    base = Path(base)
    for rel, source in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="ascii")
    return base
