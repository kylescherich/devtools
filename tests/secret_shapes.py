"""Prefixes of provider-shaped test values, split so that no file under tests/
contains a complete token shape (DEC-193). Tests build their synthetic
sentinels at runtime from these pieces; the resulting strings are unchanged.
tests/test_fixture_hygiene.py checks that no file holds a full shape."""

AKIA = "AK" + "IA"                # AWS access key id prefix
ASIA = "AS" + "IA"                # AWS temporary key id prefix
XOXB = "xo" + "xb-"               # Slack bot token
GLPAT = "gl" + "pat-"             # GitLab personal access token
AIZA = "AI" + "za"                # Google API key
SK_LIVE = "sk" + "_live_"         # Stripe secret key
RK_TEST = "rk" + "_test_"         # Stripe restricted key
GHP = "gh" + "p_"                 # GitHub personal access token
GITHUB_PAT = "github" + "_pat_"   # GitHub fine-grained token
SK_DASH = "s" + "k-"              # sk- style API key
JWT = "ey" + "J"                  # base64 of '{"', start of each JWT part


def pem(edge, kind=""):
    """A PEM armour line: pem("BEGIN", "RSA ") is the BEGIN line of an RSA
    private key; pem("END") the END line of a PKCS#8 one."""
    return "-----" + edge + " " + kind + "PRIVATE" + " KEY-----"
