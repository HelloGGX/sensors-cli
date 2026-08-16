"""Custom agent guidance for selected Ruff rule codes.

Loaded by `ruff_guidance.py` (stdlib importlib, no extra deps).
Keys must match the `code` field from `ruff check --output-format=json`.
"""

from __future__ import annotations

RULE_GUIDANCE: dict[str, dict[str, str]] = {
    "C901": {
        "short": "Function control flow is too complex — simplify",
        "guidance": """\
About C901 (McCabe complexity):
High complexity usually means too many branches/paths in one function. Prefer extracting
helpers, early returns, or smaller functions. In the exceptional case where you think the
complexity is unavoidable here, or that a refactoring would make things worse, suppress with
a noqa on that function only:
# noqa: C901 -- (brief reason)
""",
    },
    "PLR0913": {
        "short": "Too many arguments",
        "guidance": """\
About PLR0913 (too many arguments):
Many parameters often signal a missing abstraction or mixed responsibilities, and it might
make changes to this code in the future risky and hard. Consider refactoring.
If you decide that the signature is justified, you may in exceptional cases suppress on that definition only:
# noqa: PLR0913 -- (brief reason)
""",
    },
    "PLR0915": {
        "short": "Function body is too long",
        "guidance": """\
About PLR0915 (too many statements):
This counts statements, not physical lines. This is an indicator that the function is doing too much,
which might make changes to this code in the future risky and hard.
If you decide that a single cohesive unit is appropriate here, you may in exceptional cases suppress on that function only:
  # noqa: PLR0915 -- (brief reason)
""",
    },
}
