"""askdata query (M2a): SQL over local sources. STUB for the test-first run."""


class QueryError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def open_sources(specs, frozen=False, deadline=None):
    return None


def run(args, out, err):
    return 0
