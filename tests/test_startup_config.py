import pytest

from app.scripts.startup_config import main


@pytest.mark.parametrize(
    ("uri", "local"),
    [
        ("mongodb://localhost:27017", "1"),
        ("mongodb://127.0.0.1:27017/", "1"),
        ("mongodb://user:private-password@remote.test:27017", "0"),
        ("mongodb://localhost:27018", "0"),
    ],
)
def test_shell_receives_no_credentials(monkeypatch, capsys, uri, local):
    monkeypatch.setenv("MONGO_URI", uri)
    monkeypatch.setenv("API_PORT", "8001")
    main()
    assert capsys.readouterr().out == f"127.0.0.1 8001 {local}\n"
