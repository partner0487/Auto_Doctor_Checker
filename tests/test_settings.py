import settings


def test_roundtrip(tmp_path):
    p = tmp_path / "sub" / "settings.json"
    assert settings.save({"url": "https://x", "clinic": "第一", "minutes": "30", "sequence": "15"}, p)
    assert settings.load(p) == {"url": "https://x", "clinic": "第一", "minutes": "30"}  # 不存門診序號


def test_missing_file(tmp_path):
    assert settings.load(tmp_path / "nope.json") == {}


def test_corrupt_file(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json", encoding="utf-8")
    assert settings.load(p) == {}
    p.write_text("[1, 2]", encoding="utf-8")
    assert settings.load(p) == {}


def test_save_failure_returns_false(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert settings.save({"url": "a"}, blocker / "settings.json") is False


def test_default_path_uses_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert settings.default_path() == tmp_path / "AutoDoctorChecker" / "settings.json"
