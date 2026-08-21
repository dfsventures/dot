"""
WS-19 — a narrow test file for Phase-0 logic only (F-41).

Deliberately NOT a test suite for Dot as a whole — that's Phase 1. This file exists so
the specific bugs fixed in WS-14 through WS-17 cannot silently return. Only pure
functions, no fixtures beyond pytest's built-ins, no mocking of the Anthropic client,
no network calls, no CI wiring.

Run with: venv/bin/python -m pytest tests/ -q
"""
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timedelta, timezone

import agent
import memory


# ── WS-14 — event filtering (_is_preppable) ────────────────────────────────────────

def _window():
    now = datetime.now(timezone.utc)
    return now, now + timedelta(minutes=25), now + timedelta(minutes=35)


def test_all_day_event_is_not_preppable():
    """The literal Aug 7 regression: a multi-day all-day event must never be preppable,
    no matter how many 5-minute ticks it overlaps."""
    now, window_start, window_end = _window()
    event = {
        "id": "evt_laide_trip",
        "summary": "Laide & Asher's Trip",
        "start": {"date": "2026-08-07"},
        "end": {"date": "2026-08-10"},
        "attendees": [{"email": "laide.personal@gmail.com"}],
    }
    preppable, reason = agent._is_preppable(event, window_start, window_end, agent._INTERNAL_DOMAINS)
    assert preppable is False
    assert "all-day" in reason


def test_timed_event_outside_window_but_overlapping_is_not_preppable():
    """F-29: Google Calendar's timeMin/timeMax is an overlap filter, not a starts-within
    filter. A meeting that started an hour ago and is still running overlaps the query
    window but must not be prepped."""
    now, window_start, window_end = _window()
    event = {
        "id": "evt_ongoing",
        "summary": "Long-running planning session",
        "start": {"dateTime": (now - timedelta(hours=1)).isoformat()},
        "attendees": [{"email": "founder@example.com"}],
    }
    preppable, reason = agent._is_preppable(event, window_start, window_end, agent._INTERNAL_DOMAINS)
    assert preppable is False
    assert "window" in reason


def test_timed_event_starting_inside_window_with_external_attendee_is_preppable():
    now, window_start, window_end = _window()
    event = {
        "id": "evt_intro_call",
        "summary": "Intro call with Acme Fintech",
        "start": {"dateTime": (now + timedelta(minutes=30)).isoformat()},
        "attendees": [{"email": "jane@acmefintech.com"}],
    }
    preppable, reason = agent._is_preppable(event, window_start, window_end, agent._INTERNAL_DOMAINS)
    assert preppable is True


def test_internal_only_attendees_is_not_preppable():
    now, window_start, window_end = _window()
    event = {
        "id": "evt_standup",
        "summary": "DFS Lab weekly sync",
        "start": {"dateTime": (now + timedelta(minutes=30)).isoformat()},
        "attendees": [
            {"email": "me@dfslab.net", "self": True},
            {"email": "colleague@dfslab.net"},
            {"email": "other@dfs.vc"},
        ],
    }
    preppable, reason = agent._is_preppable(event, window_start, window_end, agent._INTERNAL_DOMAINS)
    assert preppable is False
    assert "external" in reason


def test_malformed_datetime_is_not_preppable():
    now, window_start, window_end = _window()
    event = {
        "id": "evt_broken",
        "summary": "Something",
        "start": {"dateTime": "not-a-real-datetime"},
        "attendees": [{"email": "someone@example.com"}],
    }
    preppable, reason = agent._is_preppable(event, window_start, window_end, agent._INTERNAL_DOMAINS)
    assert preppable is False
    assert "malformed" in reason


# ── WS-14 — mute matching (is_prep_muted) ───────────────────────────────────────────

def _scratch_mutes_conn():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript("""
        CREATE TABLE prep_mutes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern TEXT NOT NULL,
            reason TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    return conn


def test_mute_matches_exact_event_id(monkeypatch):
    conn = _scratch_mutes_conn()
    monkeypatch.setattr(memory, "conn", conn)
    memory.add_prep_mute("evt_abc123", reason="exact id test")
    assert memory.is_prep_muted("evt_abc123", "Some Random Meeting") is True


def test_mute_matches_case_insensitive_title_substring(monkeypatch):
    conn = _scratch_mutes_conn()
    monkeypatch.setattr(memory, "conn", conn)
    memory.add_prep_mute("Laide & Asher's Trip", reason="title substring test")
    assert memory.is_prep_muted("evt_other", "LAIDE & ASHER'S TRIP (updated)") is True


def test_two_char_mute_pattern_does_not_match_everything(monkeypatch):
    """Guard against a stray 2-char mute (e.g. 'ok') silencing every meeting whose title
    happens to contain it as a substring — 'Kickoff' contains 'ok'."""
    conn = _scratch_mutes_conn()
    monkeypatch.setattr(memory, "conn", conn)
    memory.add_prep_mute("ok", reason="2-char guard test")
    assert memory.is_prep_muted("evt_unrelated", "Q3 Kickoff meeting with the team") is False


def test_mute_no_match_for_unrelated_event(monkeypatch):
    conn = _scratch_mutes_conn()
    monkeypatch.setattr(memory, "conn", conn)
    memory.add_prep_mute("evt_abc123", reason="exact id test")
    assert memory.is_prep_muted("evt_xyz", "Quarterly Review") is False


# ── WS-16 — relevance-floor threshold ────────────────────────────────────────────────

class _FakeCollection:
    """Stand-in for the Chroma `_col` object — returns canned documents/distances."""

    def __init__(self, documents, distances):
        self._documents = documents
        self._distances = distances

    def count(self):
        return len(self._documents)

    def query(self, query_embeddings, n_results, include=None):
        return {"documents": [self._documents], "distances": [self._distances]}


def test_relevance_floor_filters_to_measured_survivors(monkeypatch):
    """Distances measured against the live store (see memory.py's MEMORY_DISTANCE_MAX
    comment): 0.318 and 0.493 are genuinely on-topic; 1.382 and 1.778 are the
    conversational/instructional turns that used to inject the full top-15 regardless."""
    docs  = ["on-topic A", "on-topic B", "off-topic C", "off-topic D"]
    dists = [0.318, 0.493, 1.382, 1.778]
    monkeypatch.setattr(memory, "_col", _FakeCollection(docs, dists))
    monkeypatch.setattr(memory, "_embed", lambda text: [0.0])
    results = memory.retrieve_relevant_memories("query", k=4)
    assert results == ["on-topic A", "on-topic B"]


def test_relevance_floor_empty_distances_falls_through(monkeypatch):
    """No distances returned (e.g. an older Chroma response shape) must behave exactly
    as before the floor was added — return the documents unfiltered, not an empty list."""
    docs = ["a", "b", "c"]
    monkeypatch.setattr(memory, "_col", _FakeCollection(docs, []))
    monkeypatch.setattr(memory, "_embed", lambda text: [0.0])
    results = memory.retrieve_relevant_memories("query", k=3)
    assert results == docs


# ── WS-15 — empty/thinking-only response detection ──────────────────────────────────

class _FakeThinkingBlock:
    type = "thinking"
    thinking = ""


class _FakeEmptyResponse:
    content = [_FakeThinkingBlock()]
    stop_reason = "max_tokens"


def test_response_text_returns_empty_for_thinking_only_response():
    assert memory.response_text(_FakeEmptyResponse()) == ""


def test_response_text_checked_logs_error_on_empty_response(caplog):
    with caplog.at_level(logging.ERROR):
        result = memory.response_text_checked(_FakeEmptyResponse(), "test_label")
    assert result == ""
    assert any("test_label" in r.message and "max_tokens" in r.message for r in caplog.records)


# ── WS-17 — paging marker on the (formerly truncated) cached text ──────────────────

def _paging_chunk(text: str, offset: int) -> str:
    """Mirrors the paging formula in agent.py's read_dropbox_file / read_drive_file
    exactly: return the offset..offset+3000 slice, appending a 'more characters' marker
    only when the slice doesn't reach the end of the full text."""
    chunk = text[offset:offset + 3000]
    if offset + 3000 < len(text):
        chunk += f"\n[... {len(text) - offset - 3000} more characters — call again with offset={offset + 3000}]"
    return chunk


def test_paging_marker_absent_on_a_15000_char_body_at_offset_12000():
    """Documents the F-36 bug this workstream fixes: with the old 15,000-char ingest
    truncation, offset=12000 landed exactly at the end of the cached text, so no 'more
    characters' marker ever fired and a truncated document silently looked complete."""
    text = "x" * 15000
    chunk = _paging_chunk(text, 12000)
    assert "more characters" not in chunk


def test_paging_marker_present_on_a_40000_char_body_at_offset_12000():
    """Once the cache holds the full parse (WS-17), the same offset on a genuinely long
    document correctly reports that more text remains."""
    text = "x" * 40000
    chunk = _paging_chunk(text, 12000)
    assert "more characters" in chunk
    assert "offset=15000" in chunk
