"""Prevent the default frontend CI suite from requiring an unstarted API."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_engine_required_spec_is_outside_default_e2e_directory():
    """Existing playwright.config.ts uses testDir='./tests/e2e'."""
    assert (ROOT / 'tests/engine-e2e/live-engine-required.spec.ts').is_file()
    assert not (ROOT / 'tests/e2e/live-engine-required.spec.ts').exists()
    config = (ROOT / 'playwright.live.config.ts').read_text()
    assert "testDir: './tests/engine-e2e'" in config
    assert "testMatch: 'live-engine-required.spec.ts'" in config
