"""SQL text is built from code only [P2 step 7a].

Every query interpolates nothing but identifiers that the code validates
when the store is built (schema names through `check_schema` or a regex) or
constants chosen in the code (table names, a WHERE clause, an interval
expression); values always travel as $n parameters. Bandit's B608 is skipped
for that reason (pyproject.toml), and this test keeps it true: a new
expression inside an SQL f-string fails here until it is reviewed and added.
"""

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
SQL = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|FROM|WHERE|TRUNCATE)\b")

# reviewed: what each name holds, and where it is checked
ALLOWED = {
    "self._s": "a schema name, checked when the store is built",
    "self._c": "the customer schema (oversight), checked by check_schema",
    "self._r": "the read schema, checked by projector._check",
    "self._j": "the journal schema, checked by projector._check",
    "self._schema": "the journal schema, checked by the gateway's regex",
    "s": "a local copy of self._s",
    "r": "a local copy of self._r",
    "j": "a local copy of self._j",
    "table": "a name from a constant tuple of tables",
    "where": "a constant WHERE clause written in the calling method",
    "seq": "one of two constant sequence names",
    "bucket": "a constant SQL expression chosen from INTERVALS",
    "self._summary": "the trader summary query, built from constants and the checked schema",
    "parts": "count columns built from the constant SEGMENTS and _WHERE",
    "self._tx": "the transaction union, built from a constant and the checked schema",
    "_SETTLED": "a constant condition: which transactions count as settled",
    "_TOTALS": "a constant select list of the transaction totals",
}


def interpolations() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            text = "".join(str(v.value) for v in node.values if isinstance(v, ast.Constant))
            if not SQL.search(text):
                continue
            for v in node.values:
                if isinstance(v, ast.FormattedValue):
                    where = f"{path.relative_to(SRC)}:{node.lineno}"
                    found.setdefault(ast.unparse(v.value), []).append(where)
    return found


def test_sql_strings_interpolate_only_reviewed_identifiers() -> None:
    found = interpolations()
    unknown = {k: v for k, v in found.items() if k not in ALLOWED}
    assert unknown == {}, f"review these SQL interpolations: {unknown}"
    assert "self._s" in found  # the scan sees the stores
