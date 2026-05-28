from convo_analyzer.blobs import BlobStore

def test_blobstore_roundtrip(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    h = store.put("hello world")
    assert len(h) == 64
    assert store.get(h) == "hello world"
    assert (tmp_path / "blobs" / h[:2] / f"{h}.txt").exists()

def test_blobstore_dedupes(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    h1 = store.put("same")
    h2 = store.put("same")
    assert h1 == h2
