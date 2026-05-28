# tests/test_load.py
import duckdb
from convo_analyzer.parse import parse_session_file
from convo_analyzer.load import open_db, load_session

def test_load_session_writes_all_tables(tmp_corpus, sample_session_path):
    parsed = parse_session_file(sample_session_path)
    con = open_db(tmp_corpus["db"])
    load_session(con, parsed)
    n_sessions = con.execute("select count(*) from sessions").fetchone()[0]
    n_events = con.execute("select count(*) from events").fetchone()[0]
    assert n_sessions == 1
    assert n_events == len(parsed.events)

def test_load_is_idempotent(tmp_corpus, sample_session_path):
    parsed = parse_session_file(sample_session_path)
    con = open_db(tmp_corpus["db"])
    load_session(con, parsed)
    load_session(con, parsed)  # second call replaces, doesn't duplicate
    n = con.execute("select count(*) from sessions").fetchone()[0]
    assert n == 1
