"""Windows ACL check from `icacls <path>` text.

STUB (test-first commit): never reports a grant.
"""


def others_can_read(text, owner, path=None):
    """True when an ACE in icacls text grants read to anyone but the owner,
    NT AUTHORITY\\SYSTEM or BUILTIN\\Administrators."""
    return False
