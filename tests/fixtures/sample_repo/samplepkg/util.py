import importlib


def helper(x, y=1):
    """Help."""
    pass


def load():
    importlib.import_module("samplepkg.plugins")
    __import__("json")
