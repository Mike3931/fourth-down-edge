"""A key in apps/api/.env must actually reach the provider adapter.

It did not. `Settings` ignores extra keys, so FDE_ODDS_API_KEY in a .env
file was dropped; pydantic-settings does not populate os.environ; and the
adapter read os.environ alone. The result was an operator setting the key
in the obvious, gitignored place, seeing nothing happen, and being told by
the health check to set the key they had already set.

The runbook recommended that exact non-working path, which is the part
worth a regression test rather than a fix.
"""

from __future__ import annotations

import importlib

import pytest

KEY = "probe-value-that-must-never-be-logged"


def _fresh(monkeypatch, *, env: str | None, dotenv: str | None, tmp_path):
    """Re-import config with a chosen environment and .env content."""
    import fde_api.config as config

    if env is None:
        monkeypatch.delenv("FDE_ODDS_API_KEY", raising=False)
    else:
        monkeypatch.setenv("FDE_ODDS_API_KEY", env)

    env_file = tmp_path / ".env"
    if dotenv is not None:
        env_file.write_text(dotenv, encoding="utf-8")
        monkeypatch.setattr(config, "_API_ROOT", tmp_path, raising=False)

    settings = config.Settings(
        _env_file=str(env_file) if dotenv is not None else None,  # type: ignore[call-arg]
    )
    return settings


class TestTheKeyIsReadableFromBothSources:
    def test_from_the_environment(self, monkeypatch, tmp_path) -> None:
        s = _fresh(monkeypatch, env=KEY, dotenv=None, tmp_path=tmp_path)
        assert s.has_odds_key() is True

    def test_from_a_dotenv_file(self, monkeypatch, tmp_path) -> None:
        """The case that silently did nothing."""
        s = _fresh(monkeypatch, env=None, dotenv=f"FDE_ODDS_API_KEY={KEY}\n",
                   tmp_path=tmp_path)
        assert s.odds_api_key == KEY
        assert s.has_odds_key() is True

    def test_absent_from_both(self, monkeypatch, tmp_path) -> None:
        s = _fresh(monkeypatch, env=None, dotenv=None, tmp_path=tmp_path)
        assert s.has_odds_key() is False

    def test_whitespace_is_not_a_key(self, monkeypatch, tmp_path) -> None:
        """What a half-filled .env line produces."""
        s = _fresh(monkeypatch, env=None, dotenv="FDE_ODDS_API_KEY=   \n",
                   tmp_path=tmp_path)
        assert s.has_odds_key() is False


class TestTheAdapterSeesIt:
    def test_the_adapter_is_configured_from_settings(self, monkeypatch) -> None:
        from fde_api.forward import odds

        monkeypatch.delenv("FDE_ODDS_API_KEY", raising=False)
        monkeypatch.setattr(odds, "os", importlib.import_module("os"))
        from fde_api.config import settings

        monkeypatch.setattr(settings, "odds_api_key", KEY, raising=False)
        assert odds.TheOddsApiProvider().configured is True

    def test_an_explicit_argument_still_wins(self, monkeypatch) -> None:
        from fde_api.forward.odds import TheOddsApiProvider

        monkeypatch.delenv("FDE_ODDS_API_KEY", raising=False)
        assert TheOddsApiProvider(api_key="explicit").configured is True

    def test_no_key_anywhere_refuses(self, monkeypatch) -> None:
        from fde_api.config import settings
        from fde_api.forward.odds import OddsProviderNotConfigured, TheOddsApiProvider

        monkeypatch.delenv("FDE_ODDS_API_KEY", raising=False)
        monkeypatch.setattr(settings, "odds_api_key", "", raising=False)
        with pytest.raises(OddsProviderNotConfigured):
            TheOddsApiProvider().fetch_odds()


class TestTheValueStaysOutOfSight:
    def test_it_is_absent_from_the_settings_repr(self, monkeypatch, tmp_path) -> None:
        """A logged exception carrying the settings object must not spill it."""
        s = _fresh(monkeypatch, env=None, dotenv=f"FDE_ODDS_API_KEY={KEY}\n",
                   tmp_path=tmp_path)
        assert KEY not in repr(s)
        assert "odds_api_key" not in repr(s)
