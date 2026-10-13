"""Apply reviewed Live Game Lab routing/link changes to existing app source.

Run once from the repository after adding this package's new page.
This refuses drifted or unexpected source rather than making partial edits:

    python scripts/apply_game_lab_links.py --check
    python scripts/apply_game_lab_links.py

All expected sources are taken from feature/forward-data-capture at
f388e6954ab886ab11990272d8d10cc344b9e52c; do not use on master.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Git blob hashes (LF-normalised content) from the audited feature branch at
# f388e6954ab886ab11990272d8d10cc344b9e52c. A matching edit anchor
# is NOT enough: unrelated changes elsewhere in a file can change the
# meaning of a textual insertion while still leaving the anchor intact.
AUDITED_GIT_BLOBS = {
    'apps/web/src/App.tsx': '669ab304a2a86b3e28ff23e1a07093ada65ab9f4',
    'apps/web/src/components/Shell.tsx': 'fd8e0ea65b895abc8dd94e7c288c45a45885f12e',
    'apps/web/src/pages/SlateLive.tsx': 'c972f4a48f983e5c7039583d364f84f182cba2a3',
    'apps/web/src/pages/LiveSlate.tsx': 'bdde596faaed145e3961c5de55863a0d8e67ac36',
    'apps/web/src/pages/CandidatesLive.tsx': 'c7f6ab99b4a62e546e200d8a38030528ace8cf60',
    'apps/web/src/pages/Settings.tsx': '30bc793bd02b3a34670543648847ff78132061b2',
}


# Exact reviewed post-transform blobs; anchors alone cannot authorize a rerun.
PATCHED_GIT_BLOBS = {'apps/web/src/App.tsx': 'b271f0735bc26a8103ad84743f20326cde454269', 'apps/web/src/components/Shell.tsx': '6ee4a68c32eb3838224bed1f054cd23bd129bd43', 'apps/web/src/pages/SlateLive.tsx': 'dc29854c7c587cb423cf84f8a619e207af493500', 'apps/web/src/pages/LiveSlate.tsx': 'f3fb7952860071a4077102a95336722a5125432f', 'apps/web/src/pages/CandidatesLive.tsx': 'b419c33fa00780246f46134b10953c220899ecac', 'apps/web/src/pages/Settings.tsx': '63315d6a13f5528df21c554fc59fba04d6e05a6c'}

CORRECTIVE_GIT_BLOBS = {'apps/web/src/pages/SlateLive.tsx': 'ab54836e3835a69dc5ea9753c4ab8b87461a6102'}

def git_blob_sha(text: str) -> str:
    content = text.encode('utf-8')
    return hashlib.sha1(b'blob ' + str(len(content)).encode('ascii') + b'\0' + content).hexdigest()


def replacement(content: str, original: str, replacement: str, name: str) -> str:
    if replacement in content:
        return content  # already applied, including when original is its prefix
    count = content.count(original)
    if count != 1:
        raise ValueError(f'{name}: expected exactly one original anchor, found {count}; aborting')
    return content.replace(original, replacement, 1)


def prepare(root: Path, *, enforce_audited_blobs: bool = True) -> dict[Path, str]:
    edits: dict[str, list[tuple[str, str]]] = {
        'apps/web/src/App.tsx': [
            (
                "import LiveSlate from './pages/LiveSlate';\n",
                "import LiveSlate from './pages/LiveSlate';\nimport LiveGameLab from './pages/LiveGameLab';\n",
            ),
            (
                '        <Route path="/live" element={<LiveSlate />} />\n',
                '        <Route path="/live" element={<LiveSlate />} />\n'
                '        <Route path="/live/game/:gameId" element={<LiveGameLab />} />\n',
            ),
        ],
        'apps/web/src/components/Shell.tsx': [
            (
                'function isLiveRoute(pathname: string): boolean {\n  return LIVE_ROUTES.has(pathname);\n}',
                "function isLiveRoute(pathname: string): boolean {\n"
                "  return LIVE_ROUTES.has(pathname) || pathname.startsWith('/live/game/');\n}",
            ),
            (
                "label: 'Game Lab', icon: '⚗', live: false, group: DEMO_GROUP",
                "label: 'Game Lab (demo)', icon: '⚗', live: false, group: DEMO_GROUP",
            ),
        ],
        'apps/web/src/pages/SlateLive.tsx': [
            (
                "import { useEffect, useMemo, useState } from 'react';\n",
                "import { useEffect, useMemo, useState } from 'react';\nimport { Link } from 'react-router-dom';\n",
            ),
            (
                '<td className="py-1.5 pr-4 font-medium text-fg">\n'
                '                  {g.away_team_id} @ {g.home_team_id}\n',
                '<td className="py-1.5 pr-4 font-medium text-fg">\n'
                '                  <Link to={`/live/game/${encodeURIComponent(g.canonical_game_id)}`} className="text-accent hover:underline">\n'
                '                    {g.away_team_id} @ {g.home_team_id}\n'
                '                  </Link>\n',
            ),
        ],
        'apps/web/src/pages/LiveSlate.tsx': [
            (
                '<h2 className="text-lg font-semibold text-fg">\n'
                '                {g.away_team_id} @ {g.home_team_id}\n'
                '              </h2>',
                '<h2 className="text-lg font-semibold text-fg">\n'
                '                <Link to={`/live/game/${encodeURIComponent(g.canonical_game_id)}`} className="text-accent hover:underline">\n'
                '                  {g.away_team_id} @ {g.home_team_id}\n'
                '                </Link>\n'
                '              </h2>',
            ),
        ],
        'apps/web/src/pages/CandidatesLive.tsx': [
            (
                '<td className="py-1.5 pr-4 text-muted">\n'
                '                    {c.away_team_id} @ {c.home_team_id}\n'
                '                  </td>',
                '<td className="py-1.5 pr-4 text-muted">\n'
                '                    <Link to={`/live/game/${encodeURIComponent(c.canonical_game_id)}`} className="text-accent hover:underline">\n'
                '                      {c.away_team_id} @ {c.home_team_id}\n'
                '                    </Link>\n'
                '                  </td>',
            ),
        ],
        'apps/web/src/pages/Settings.tsx': [
            (
                "import { fmtPct } from '../lib/format';\n",
                "import { fmtPct } from '../lib/format';\nimport { ENGINE_BASE } from '../lib/engine';\n",
            ),
            (
                '<CardHeader title="Analytical engine (research mode)" />',
                '<CardHeader title="Legacy Game Lab research connection (demo section)" />',
            ),
            (
                '            When connected, Game Lab shows research-grade predictions from the Python engine under the\n',
                '            This setting applies to the legacy demonstration Game Lab, not the real NFL screens.\n'
                '            Live screens use the configured backend path <code>{ENGINE_BASE}</code>; changing this field\n'
                '            does not change that connection. When connected, the legacy Game Lab shows research results under the\n',
            ),
        ],
    }
    changes: dict[Path, str] = {}
    for relative, replacements in edits.items():
        file = root / relative
        # Preserve Windows CRLF if the repository was checked out with it.
        # Path.read_text() normalises CRLF silently; a naïve write rewrites
        # whole files and pollutes the audit diff.
        raw = file.read_bytes().decode('utf-8')
        newline = '\r\n' if '\r\n' in raw else '\n'
        before = raw.replace('\r\n', '\n')
        if enforce_audited_blobs:
            expected = AUDITED_GIT_BLOBS[relative]
            actual = git_blob_sha(before)
            # Re-running on our own fully-patched files is safe and useful.
            # Partial edits or divergent source require a manual diff review.
            if actual not in (expected, PATCHED_GIT_BLOBS[relative], CORRECTIVE_GIT_BLOBS.get(relative)):
                raise ValueError(
                    f'{relative}: source drift detected; audited git blob '
                    f'{expected}, found {actual}. Review and port manually.'
                )
        after = before
        for original, target in replacements:
            after = replacement(after, original, target, relative)
        if after != before:
            changes[file] = after.replace('\n', newline)
    page = root / 'apps/web/src/pages/LiveGameLab.tsx'
    if not page.is_file():
        raise FileNotFoundError(f'Missing page; apply new-files patch first: {page}')
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check anchors without editing files')
    args = parser.parse_args(argv)
    try:
        changes = prepare(ROOT)
    except (OSError, ValueError) as exc:
        print(f'PATCH_REFUSED: {exc}', file=sys.stderr)
        return 2
    if not args.check:
        for file, after in changes.items():
            file.write_bytes(after.encode('utf-8'))
    print(f'{len(changes)} existing files {"can be" if args.check else "were"} updated.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
