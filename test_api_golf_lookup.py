"""Regression guard for the api.py:698 NameError in golf_course_lookup.

The ``top_score == 0`` ("not_found" with candidates) branch of
``golf_course_lookup`` once built its result dict with ``"query": query`` —
but no variable named ``query`` exists in that scope. The loop variable is
``q`` and the tracked value is ``last_query_used`` (defined just before the
search loop). Because the route wraps everything in a blanket
``except Exception``, the resulting ``NameError`` was swallowed and every
zero-score course came back as ``status="error"`` ("name 'query' is not
defined") instead of the intended ``status="not_found"`` with a
``candidates`` list. The Fly copy (bridge-extract/app.py) was already fixed
to use ``last_query_used``.

This test parses api.py with the ``ast`` module and asserts that the
``golf_course_lookup`` function body contains no bare ``query`` name loads in
its own scope. The nested ``_search_with_retry`` helper has a legitimate
``query`` parameter, so it is excluded from the check. Fast, no network.
"""

import ast
import os

API_PATH = os.path.join(os.path.dirname(__file__), "api.py")


def _find_function(tree: ast.AST, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found in api.py")


def _bare_query_loads_excluding_nested(func: ast.AST) -> list[ast.Name]:
    """Collect ``ast.Name`` loads named exactly 'query' that belong to *func*'s
    own scope — i.e. not inside any nested function/lambda that introduces its
    own ``query`` binding (such as ``_search_with_retry``)."""
    # Walk every nested scope and record the id() of all Name nodes within them.
    nested_names: set[int] = set()
    for node in ast.walk(func):
        if node is func:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for sub in ast.walk(node):
                nested_names.add(id(sub))

    hits: list[ast.Name] = []
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Name)
            and node.id == "query"
            and isinstance(node.ctx, ast.Load)
            and id(node) not in nested_names
        ):
            hits.append(node)
    return hits


def test_golf_course_lookup_has_no_bare_query_nameerror():
    with open(API_PATH, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=API_PATH)

    func = _find_function(tree, "golf_course_lookup")
    hits = _bare_query_loads_excluding_nested(func)

    assert not hits, (
        "golf_course_lookup references an undefined bare `query` variable at "
        + ", ".join(f"line {n.lineno}" for n in hits)
        + " — use `last_query_used` (the query string actually used) instead. "
        "This guards the api.py NameError regression in the not_found branch."
    )
