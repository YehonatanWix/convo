# tests/test_ingest.py
import shutil
from convo_analyzer.ingest import ingest_all

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
