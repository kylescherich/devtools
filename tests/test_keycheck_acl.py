"""keycheck Windows ACL parsing on captured `icacls` text (BRIEF-DEVWS-041).
Text samples only; no real ACL is read or changed."""
import unittest

from keycheck.acl import others_can_read

OWNER = "DESKTOP-AB12\\kyle"
PATH = "C:\\Users\\kyle\\.ssh\\id_ed25519"
TAIL = "\nSuccessfully processed 1 files; Failed processing 0 files\n"
PAD = " " * (len(PATH) + 1)


def sample(*aces):
    lines = [f"{PATH} {aces[0]}"] + [PAD + ace for ace in aces[1:]]
    return "\n".join(lines) + "\n" + TAIL


OWNER_ONLY = sample("DESKTOP-AB12\\kyle:(F)", "NT AUTHORITY\\SYSTEM:(F)",
                    "BUILTIN\\Administrators:(F)")
USERS_R = sample("DESKTOP-AB12\\kyle:(F)", "NT AUTHORITY\\SYSTEM:(F)",
                 "BUILTIN\\Users:(R)")
EVERYONE_F = sample("Everyone:(F)", "DESKTOP-AB12\\kyle:(F)")
AUTH_USERS_INHERITED = sample("DESKTOP-AB12\\kyle:(I)(F)",
                              "NT AUTHORITY\\SYSTEM:(I)(F)",
                              "BUILTIN\\Administrators:(I)(F)",
                              "NT AUTHORITY\\Authenticated Users:(I)(RX)")


class TestIcacls(unittest.TestCase):
    def check(self, text, expected, owner=OWNER):
        self.assertTrue(text)
        self.assertIs(others_can_read(text, owner, PATH), expected)

    def test_owner_only(self):
        self.check(OWNER_ONLY, False)

    def test_users_read(self):
        self.check(USERS_R, True)

    def test_everyone_full(self):
        self.check(EVERYONE_F, True)

    def test_inherited_authenticated_users_rx(self):
        self.check(AUTH_USERS_INHERITED, True)

    def test_modify_counts(self):
        self.check(sample("DESKTOP-AB12\\kyle:(F)", "DESKTOP-AB12\\other:(M)"), True)

    def test_owner_case_insensitive(self):
        self.check(sample("desktop-ab12\\KYLE:(F)", "NT AUTHORITY\\SYSTEM:(F)"), False)

    def test_another_user_is_flagged(self):
        self.check(OWNER_ONLY, True, owner="DESKTOP-AB12\\someone")

    def test_write_only_deny_and_inherit_only_ignored(self):
        self.check(sample("DESKTOP-AB12\\kyle:(F)", "BUILTIN\\Users:(W)",
                          "Everyone:(DENY)(R)", "CREATOR OWNER:(OI)(CI)(IO)(F)"), False)

    def test_path_with_spaces_and_colon(self):
        path = "C:\\Users\\kyle\\My Keys\\gate.key"
        text = (f"{path} DESKTOP-AB12\\kyle:(F)\n"
                f"{' ' * (len(path) + 1)}BUILTIN\\Users:(RX)\n" + TAIL)
        self.assertTrue(text)
        self.assertIs(others_can_read(text, OWNER, path), True)


if __name__ == "__main__":
    unittest.main()
