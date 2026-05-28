from convo_analyzer.sanitize import scrub, normalize_path

def test_scrub_aws_key():
    s = "AKIAIOSFODNN7EXAMPLE is bad"
    out = scrub(s)
    assert "AKIA" not in out
    assert "[REDACTED:aws]" in out

def test_scrub_github_token():
    s = "token=ghp_abcdefghijklmnopqrstuvwxyz0123456789 here"
    out = scrub(s)
    assert "ghp_" not in out

def test_scrub_bearer():
    s = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
    out = scrub(s)
    assert "eyJ" not in out

def test_scrub_env_line():
    s = "DATABASE_PASSWORD=hunter2\nOK"
    out = scrub(s)
    assert "hunter2" not in out

def test_normalize_path():
    assert normalize_path("/Users/yehonatana/foo/bar") == "~/foo/bar"
    assert normalize_path("nothing") == "nothing"
