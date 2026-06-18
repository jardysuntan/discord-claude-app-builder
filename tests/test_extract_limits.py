"""Unit tests for extract_limits.py (root copy) — per-account daily spend cap.

Public API (from the code):
  - check_and_consume(account_id, *, is_admin=False) -> (allowed, used_today, limit)
  - status(account_id) -> {"date", "count", "limit"}

State lives in a JSON file at module global `_path`; caps come from module
globals DAILY_LIMIT / GLOBAL_DAILY_CAP (read from env at import). "today" comes
from _today() (UTC strftime). We monkeypatch _path, the cap globals, and _today
so tests are hermetic and exercise rollover.
"""

import json

import pytest

import extract_limits as el


@pytest.fixture
def fresh_state(tmp_path, monkeypatch):
    """Point the module at a tmp state file with a small, deterministic cap."""
    state = tmp_path / "extract_limits.json"
    monkeypatch.setattr(el, "_path", state)
    monkeypatch.setattr(el, "DAILY_LIMIT", 2)
    monkeypatch.setattr(el, "GLOBAL_DAILY_CAP", 100)
    monkeypatch.setattr(el, "_today", lambda: "2026-06-17")
    return state


def test_under_cap_allows_and_persists(fresh_state):
    allowed, used, limit = el.check_and_consume("acct1")
    assert allowed is True
    assert used == 1
    assert limit == 2
    # persisted to disk
    data = json.loads(fresh_state.read_text())
    assert data["acct1"]["count"] == 1
    assert data["acct1"]["date"] == "2026-06-17"


def test_consume_increments_until_cap(fresh_state):
    assert el.check_and_consume("acct1")[0] is True   # 1
    assert el.check_and_consume("acct1")[0] is True   # 2 (== limit)
    allowed, used, limit = el.check_and_consume("acct1")  # 3rd denied
    assert allowed is False
    assert used == 2
    assert limit == 2


def test_deny_does_not_consume_or_persist_more(fresh_state):
    el.check_and_consume("acct1")
    el.check_and_consume("acct1")  # now at cap (2)
    before = json.loads(fresh_state.read_text())["acct1"]["count"]
    el.check_and_consume("acct1")  # denied
    after = json.loads(fresh_state.read_text())["acct1"]["count"]
    assert before == after == 2


def test_admin_bypasses_and_does_not_consume(fresh_state):
    allowed, used, limit = el.check_and_consume("admin_acct", is_admin=True)
    assert allowed is True
    assert used == 0          # admin path returns 0 used
    # No state file written by the admin path.
    assert not fresh_state.exists()


def test_admin_bypass_even_when_others_at_cap(fresh_state):
    el.check_and_consume("acct1")
    el.check_and_consume("acct1")  # acct1 at cap
    assert el.check_and_consume("admin_acct", is_admin=True)[0] is True


def test_per_account_isolation(fresh_state):
    el.check_and_consume("acct1")
    el.check_and_consume("acct1")  # acct1 at cap
    # A different account still has its own quota.
    assert el.check_and_consume("acct2")[0] is True


def test_utc_day_rollover_resets_quota(fresh_state, monkeypatch):
    el.check_and_consume("acct1")
    el.check_and_consume("acct1")
    assert el.check_and_consume("acct1")[0] is False  # capped today
    # Advance the clock to a new UTC day.
    monkeypatch.setattr(el, "_today", lambda: "2026-06-18")
    allowed, used, limit = el.check_and_consume("acct1")
    assert allowed is True
    assert used == 1   # counter reset for the new day


def test_global_cap_blocks_when_total_reached(fresh_state, monkeypatch):
    monkeypatch.setattr(el, "GLOBAL_DAILY_CAP", 1)
    assert el.check_and_consume("acct1")[0] is True   # global count -> 1
    # Even a brand-new account is blocked once the global cap is hit.
    allowed, used, limit = el.check_and_consume("acct2")
    assert allowed is False
    assert limit == 1   # the returned limit is the GLOBAL cap on this path


def test_status_does_not_consume(fresh_state):
    el.check_and_consume("acct1")  # count -> 1
    s = el.status("acct1")
    assert s["count"] == 1
    assert s["limit"] == 2
    assert s["date"] == "2026-06-17"
    # status() must not increment.
    s2 = el.status("acct1")
    assert s2["count"] == 1


def test_status_unknown_account_is_zero(fresh_state):
    s = el.status("never-seen")
    assert s == {"date": "2026-06-17", "count": 0, "limit": 2}


def test_status_stale_day_reports_zero(fresh_state, monkeypatch):
    el.check_and_consume("acct1")  # recorded under 2026-06-17
    monkeypatch.setattr(el, "_today", lambda: "2026-06-18")
    s = el.status("acct1")
    assert s["count"] == 0   # yesterday's count not carried forward
