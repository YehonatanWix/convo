import pathlib
import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

@pytest.fixture
def sample_session_path() -> pathlib.Path:
    return FIXTURES / "sample_session.jsonl"

@pytest.fixture
def tmp_corpus(tmp_path):
    return {
        "db": tmp_path / "corpus.db",
        "blobs": tmp_path / "blobs",
        "manifest": tmp_path / "manifest.json",
    }
