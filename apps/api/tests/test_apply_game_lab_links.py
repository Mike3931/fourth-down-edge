"""Validate safe, idempotent source transforms on repo-shaped fixtures.

This checks anchor discipline, not a build against the real repository.
The real branch must still pass TypeScript/Playwright gates.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / 'scripts/apply_game_lab_links.py'


def load_script():
    spec = importlib.util.spec_from_file_location('apply_game_lab_links', SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FRAGMENTS = {
    'apps/web/src/App.tsx': (
        "import LiveSlate from './pages/LiveSlate';\n"
        '        <Route path="/live" element={<LiveSlate />} />\n'
    ),
    'apps/web/src/components/Shell.tsx': (
        "label: 'Game Lab', icon: '⚗', live: false, group: DEMO_GROUP\n"
        'function isLiveRoute(pathname: string): boolean {\n  return LIVE_ROUTES.has(pathname);\n}\n'
    ),
    'apps/web/src/pages/SlateLive.tsx': (
        "import { useEffect, useMemo, useState } from 'react';\n"
        '<td className="py-1.5 pr-4 font-medium text-fg">\n'
        '                  {g.away_team_id} @ {g.home_team_id}\n'
    ),
    'apps/web/src/pages/LiveSlate.tsx': (
        '<h2 className="text-lg font-semibold text-fg">\n'
        '                {g.away_team_id} @ {g.home_team_id}\n'
        '              </h2>\n'
    ),
    'apps/web/src/pages/CandidatesLive.tsx': (
        '<td className="py-1.5 pr-4 text-muted">\n'
        '                    {c.away_team_id} @ {c.home_team_id}\n'
        '                  </td>\n'
    ),
    'apps/web/src/pages/Settings.tsx': (
        "import { fmtPct } from '../lib/format';\n"
        '<CardHeader title="Analytical engine (research mode)" />\n'
        '            When connected, Game Lab shows research-grade predictions from the Python engine under the\n'
    ),
}


def make_repo(tmp_path):
    for name, text in FRAGMENTS.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode('utf-8'))
    lab = tmp_path / 'apps/web/src/pages/LiveGameLab.tsx'
    lab.write_text('export default function LiveGameLab() {}\n')


def test_all_anchors_replaced_then_idempotent(tmp_path):
    mod = load_script()
    make_repo(tmp_path)
    changes = mod.prepare(tmp_path, enforce_audited_blobs=False)
    assert len(changes) == len(FRAGMENTS)
    for path, new_source in changes.items():
        path.write_bytes(new_source.encode('utf-8'))
    assert mod.prepare(tmp_path, enforce_audited_blobs=False) == {}  # rerun does not duplicate imports/routes
    assert 'REAL' not in (tmp_path / 'apps/web/src/pages/LiveSlate.tsx').read_text()
    assert '/live/game/' in (tmp_path / 'apps/web/src/pages/CandidatesLive.tsx').read_text()
    assert "pathname.startsWith('/live/game/')" in (tmp_path / 'apps/web/src/components/Shell.tsx').read_text()


def test_unknown_source_refuses_without_mutation(tmp_path):
    mod = load_script()
    make_repo(tmp_path)
    bad = tmp_path / 'apps/web/src/pages/SlateLive.tsx'
    bad.write_text('unexpected different implementation\n')
    original = {p: (tmp_path / p).read_bytes() for p in FRAGMENTS}
    try:
        mod.prepare(tmp_path, enforce_audited_blobs=False)
    except ValueError as exc:
        assert 'expected exactly one original anchor' in str(exc)
    else:
        raise AssertionError('Must refuse on drift')
    assert original == {p: (tmp_path / p).read_bytes() for p in FRAGMENTS}


def test_crlf_checkout_retained_in_all_modified_files(tmp_path):
    mod = load_script()
    make_repo(tmp_path)
    for name in FRAGMENTS:
        p = tmp_path / name
        p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    changes = mod.prepare(tmp_path, enforce_audited_blobs=False)
    assert len(changes) == len(FRAGMENTS)
    for file, changed in changes.items():
        assert '\r\n' in changed
        assert changed.replace('\r\n', '').find('\n') == -1
        file.write_bytes(changed.encode('utf-8'))
    assert mod.prepare(tmp_path, enforce_audited_blobs=False) == {}


def test_audited_hash_gate_rejects_unrecognized_source_even_when_anchors_match(tmp_path):
    mod = load_script()
    make_repo(tmp_path)
    # The exact anchor is present, but the surrounding source is not the
    # approved GitHub blob. This must fail before any existing file changes.
    try:
        mod.prepare(tmp_path)
    except ValueError as exc:
        assert 'source drift detected' in str(exc)
    else:
        raise AssertionError('Unapproved source with matching anchors must be refused')


def test_git_blob_sha_matches_canonical_git_object_hash():
    import hashlib
    mod = load_script()
    content = 'sample\n'
    payload = content.encode()
    expected = hashlib.sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest()
    assert mod.git_blob_sha(content) == expected


def test_rerun_with_all_targets_but_unrelated_drift_is_refused(tmp_path):
    mod = load_script()
    make_repo(tmp_path)
    for file, changed in mod.prepare(tmp_path, enforce_audited_blobs=False).items():
        file.write_bytes((changed + '\n// unexpected surrounding source\n').encode('utf-8'))
    try:
        mod.prepare(tmp_path)
    except ValueError as exc:
        assert 'source drift detected' in str(exc)
    else:
        raise AssertionError('Fully inserted anchors must not bypass source hashes')
