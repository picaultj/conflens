import os

from conflens.cli import _load_env


def test_load_env_reads_dotenv_from_run_directory(tmp_path, monkeypatch):
    """A .env in the directory conflens is launched from is picked up.

    Guards the pip-installed case: `find_dotenv(usecwd=True)` must search the
    working directory, not the installed package location.
    """
    (tmp_path / ".env").write_text("CONFLENS_ENV_PROBE=hello\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CONFLENS_ENV_PROBE", raising=False)
    try:
        _load_env()
        assert os.environ.get("CONFLENS_ENV_PROBE") == "hello"
    finally:
        os.environ.pop("CONFLENS_ENV_PROBE", None)  # load_dotenv sets os.environ directly


def test_load_env_no_dotenv_is_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # empty dir, no .env
    monkeypatch.delenv("CONFLENS_ENV_PROBE", raising=False)
    _load_env()  # must not raise
    assert os.environ.get("CONFLENS_ENV_PROBE") is None


def test_real_env_var_takes_precedence_over_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("CONFLENS_ENV_PROBE=from_dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONFLENS_ENV_PROBE", "from_real_env")
    _load_env()
    assert os.environ.get("CONFLENS_ENV_PROBE") == "from_real_env"  # not overridden
