# tests/test_manifest.py
import json
from convo_analyzer.manifest import Manifest

def test_manifest_records_and_checks(tmp_path):
    m = Manifest(tmp_path / "manifest.json")
    assert not m.has_seen("s1", "2026-05-01T00:00:00Z")
    m.mark("s1", "2026-05-01T00:00:00Z")
    m.save()
    m2 = Manifest(tmp_path / "manifest.json")
    assert m2.has_seen("s1", "2026-05-01T00:00:00Z")
    # newer ts is not seen yet
    assert not m2.has_seen("s1", "2026-05-02T00:00:00Z")
