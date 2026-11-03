"""Every capped child gets fixed thread and arena limits (core/childenv.py)."""

from readablestem.core.childenv import child_env
from readablestem.web.isolated_convert import child_environment


def test_child_env_caps_threads_and_arenas_for_every_capped_child(monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "64")  # the parent's own value must not leak through
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-for-children")
    for env in (child_env(), child_environment()):
        assert env["OPENBLAS_NUM_THREADS"] == "1"
        assert env["OMP_NUM_THREADS"] == "1"
        assert env["MKL_NUM_THREADS"] == "1"
        assert env["MALLOC_ARENA_MAX"] == "2"
        assert "ANTHROPIC_API_KEY" not in env
