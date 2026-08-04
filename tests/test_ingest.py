# tests/test_ingest.py
import pathlib
import shutil
from convo_analyzer.ingest import ingest_all

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "nested_project"

def test_ingest_all_one_session(tmp_path, tmp_corpus, sample_session_path):
    projects_root = tmp_path / "projects"
    pdir = projects_root / "-Users-yehonatana-Workspace-Wix-foo"
    pdir.mkdir(parents=True)
    shutil.copy(sample_session_path, pdir / "abc-123.jsonl")
    stats = ingest_all(
        projects_root=projects_root,
        db_path=tmp_corpus["db"],
        blobs_path=tmp_corpus["blobs"],
        manifest_path=tmp_corpus["manifest"],
    )
    assert stats["sessions_ingested"] == 1
    # rerun is a no-op
    stats2 = ingest_all(
        projects_root=projects_root,
        db_path=tmp_corpus["db"],
        blobs_path=tmp_corpus["blobs"],
        manifest_path=tmp_corpus["manifest"],
    )
    assert stats2["sessions_ingested"] == 0


def test_ingest_links_subagents(tmp_corpus):
    result = ingest_all(
        projects_root=FIXTURES,
        db_path=tmp_corpus["db"],
        blobs_path=tmp_corpus["blobs"],
        manifest_path=tmp_corpus["manifest"],
    )
    assert result["sessions_ingested"] == 2

    import duckdb
    con = duckdb.connect(str(tmp_corpus["db"]))
    rows = con.execute(
        "SELECT session_id, project, is_subagent, parent_session_id "
        "FROM sessions ORDER BY is_subagent"
    ).fetchall()
    parent_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert rows[0] == (parent_id, "project", False, None)
    assert rows[1][2] is True
    assert rows[1][3] == parent_id
    assert rows[1][1] == "project"

    n = con.execute("""
        SELECT COUNT(*) FROM tool_calls tc
        JOIN sessions s USING (session_id)
        WHERE (s.session_id = ? OR s.parent_session_id = ?)
          AND tc.tool_name = 'LSP'
    """, [parent_id, parent_id]).fetchone()[0]
    assert n == 1
