"""Unit tests for the pure string guards in supabase_client.py.

  - check_destructive(sql) -> list[str] of human-readable warnings (empty == clean).
  - extract_sql(text)      -> Optional[str] (None when no SQL found).

Importing supabase_client pulls in aiohttp + config at module top; both are
installed in the venv so the import is fine. We only exercise the pure funcs.
"""

import supabase_client as sc


# ── check_destructive ─────────────────────────────────────────────────────────

def test_clean_select_passes():
    assert sc.check_destructive("SELECT * FROM trips;") == []


def test_clean_insert_passes():
    assert sc.check_destructive("INSERT INTO trips (id) VALUES (1);") == []


def test_create_table_passes():
    assert sc.check_destructive("CREATE TABLE foo (id int);") == []


def test_delete_with_where_passes():
    # The DELETE pattern only fires for "DELETE FROM <table>;" (no WHERE).
    assert sc.check_destructive("DELETE FROM trips WHERE id = 1;") == []


def test_drop_table_without_if_exists_flagged():
    warnings = sc.check_destructive("DROP TABLE trips;")
    assert warnings == ["DROP TABLE without IF EXISTS"]


def test_drop_table_if_exists_not_flagged():
    # IF EXISTS form is considered safe by the negative lookahead.
    assert sc.check_destructive("DROP TABLE IF EXISTS trips;") == []


def test_drop_schema_flagged():
    assert "DROP SCHEMA" in sc.check_destructive("DROP SCHEMA public CASCADE;")


def test_truncate_flagged():
    assert "TRUNCATE" in sc.check_destructive("TRUNCATE trips;")


def test_delete_without_where_flagged():
    assert "DELETE FROM without WHERE clause" in sc.check_destructive("DELETE FROM trips;")


def test_case_insensitive():
    assert sc.check_destructive("drop table trips;") == ["DROP TABLE without IF EXISTS"]
    assert "TRUNCATE" in sc.check_destructive("truncate trips;")


def test_multiple_destructive_ops_all_reported():
    sql = "DROP SCHEMA public; TRUNCATE foo;"
    warnings = sc.check_destructive(sql)
    assert "DROP SCHEMA" in warnings
    assert "TRUNCATE" in warnings


# ── extract_sql ───────────────────────────────────────────────────────────────

def test_extract_sql_fenced_block():
    text = "Here is the SQL:\n```sql\nCREATE TABLE foo (id int);\n```\nDone."
    assert sc.extract_sql(text) == "CREATE TABLE foo (id int);"


def test_extract_sql_unlabeled_fence_with_create_table():
    text = "```\nCREATE TABLE bar (id int);\n```"
    assert sc.extract_sql(text) == "CREATE TABLE bar (id int);"


def test_extract_sql_unlabeled_fence_without_create_table_ignored_then_bare():
    # An unlabeled fence lacking CREATE TABLE is skipped; falls through to the
    # bare-SQL matcher, which finds nothing here -> None.
    text = "```\nSELECT 1;\n```"
    assert sc.extract_sql(text) is None


def test_extract_sql_bare_create_table():
    text = "CREATE TABLE baz (id int, name text);"
    assert sc.extract_sql(text) == "CREATE TABLE baz (id int, name text);"


def test_extract_sql_bare_create_table_with_prose_prefix():
    text = "Sure! CREATE TABLE qux (id int);"
    # Bare matcher grabs from CREATE TABLE onward.
    assert sc.extract_sql(text) == "CREATE TABLE qux (id int);"


def test_extract_sql_returns_none_when_no_sql():
    assert sc.extract_sql("there is no sql here at all") is None


def test_extract_sql_fenced_takes_precedence_over_bare():
    text = "```sql\nCREATE TABLE a (id int);\n```\nignore CREATE TABLE b (id int);"
    assert sc.extract_sql(text) == "CREATE TABLE a (id int);"
