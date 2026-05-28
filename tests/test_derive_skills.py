# tests/test_derive_skills.py
from convo_analyzer.load import open_db
from convo_analyzer.derive.skills import build_skill_signals

def test_skill_eligible_missed(tmp_corpus, tmp_path):
    yaml_path = tmp_path / "skills.yaml"
    yaml_path.write_text(
        'pretend_skill:\n  signature: ["Read","Edit"]\n  reason: "x"\n'
    )
    con = open_db(tmp_corpus["db"])
    for i, name in enumerate(["Read","Edit"]):
        con.execute(
            "INSERT INTO tool_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [f"e{i}", "s1", f"2026-01-01T00:00:0{i}Z", name,
             "{}", None, 0, None, True, None, 0, i],
        )
    build_skill_signals(con, skills_yaml=yaml_path)
    rows = con.execute(
        "SELECT skill_name FROM signal_skill_eligible_missed"
    ).fetchall()
    assert ("pretend_skill",) in rows
