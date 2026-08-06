"""Flipping the legacy flag must change nothing that matters.

The no-write and no-read audits are source-level: they prove no current
service assigns or selects on `consensus_snapshots.is_closing_capture`.
Source inspection can be defeated by a path nobody read, so this proves the
same thing behaviourally — set the flag to whatever you like and watch the
answers not move.

Two readers are classified as legitimate, and each is tested for what it
may and may not do:

  chain.py       a compatibility CENSUS. It may count legacy rows; it may
                 not let them change a current answer.
  consensus.py   isolates pre-migration rows from `latest_consensus_at`.
                 It may not choose a close, or change a consensus value,
                 CLV, settlement, parity or forward performance.
"""

from __future__ import annotations

from sqlalchemy import select

from fde_api.db.forward_models import (
    ClosingCapture,
    ConsensusSnapshot,
    ForwardLedgerEntry,
)
from fde_api.forward.chain import reconcile_chain
from fde_api.forward.closing import authoritative_capture, closing_snapshot_for
from fde_api.forward.semantic_hash import build_semantic_chain, compare


def _flip_every_flag(session) -> int:
    """Set the legacy flag on every consensus snapshot.

    The most hostile form of the question: not "does one stray row matter"
    but "does the flag matter at all".
    """
    rows = list(session.scalars(select(ConsensusSnapshot)))
    for row in rows:
        row.is_closing_capture = True
    session.commit()
    return len(rows)


def _semantic(factory):
    from chainkit import COHORT, DATA_MODE, GAME, POLICY

    with factory() as s:
        return build_semantic_chain(
            s, canonical_game_id=GAME, data_mode=DATA_MODE.value,
            cohort=COHORT.value, policy_version=POLICY)


class TestTheLegacyFlagCannotChangeCurrentAnswers:
    def test_the_authoritative_close_is_unchanged(self, chain) -> None:
        from chainkit import COHORT, GAME

        with chain.session() as s:
            before = authoritative_capture(
                s, canonical_game_id=GAME, market="SPREAD", cohort=COHORT)
            assert before is not None
            identity = (before.id, before.status, before.consensus_snapshot_id)
            flipped = _flip_every_flag(s)
            assert flipped > 0, "no snapshots to flip; the test proves nothing"
            after = authoritative_capture(
                s, canonical_game_id=GAME, market="SPREAD", cohort=COHORT)
        assert after is not None
        assert (after.id, after.status, after.consensus_snapshot_id) == identity

    def test_the_referenced_closing_snapshot_is_unchanged(self, chain) -> None:
        from chainkit import COHORT, GAME

        with chain.session() as s:
            before = closing_snapshot_for(
                s, canonical_game_id=GAME, market="SPREAD", cohort=COHORT)
            before_id = before.id if before else None
            _flip_every_flag(s)
            after = closing_snapshot_for(
                s, canonical_game_id=GAME, market="SPREAD", cohort=COHORT)
        assert (after.id if after else None) == before_id

    def test_the_semantic_chain_hash_is_unchanged(self, chain) -> None:
        """`chain-semantic-v2` must not depend on archival state."""
        before = _semantic(chain.factory)
        with chain.session() as s:
            _flip_every_flag(s)
        after = _semantic(chain.factory)
        diff = compare(before, after)
        assert diff["equal"], diff["differing"][:3]

    def test_clv_and_settlement_are_unchanged(self, chain) -> None:
        with chain.session() as s:
            before = sorted(
                (e.id, e.result, e.pnl_units, e.clv_line, e.clv_probability,
                 e.closing_line, e.closing_american)
                for e in s.scalars(select(ForwardLedgerEntry))
            )
            _flip_every_flag(s)
            after = sorted(
                (e.id, e.result, e.pnl_units, e.clv_line, e.clv_probability,
                 e.closing_line, e.closing_american)
                for e in s.scalars(select(ForwardLedgerEntry))
            )
        assert after == before

    def test_recomputed_clv_is_unchanged(self, chain) -> None:
        """Not just the stored value - recomputing must also agree."""
        from chainkit import DATA_MODE, KICK, POLICY
        from fde_api.forward.ledger import compute_clv
        from fde_api.forward.policy import load_policy

        with chain.session() as s:
            policy = load_policy(s, POLICY)
            entry = s.scalars(select(ForwardLedgerEntry)).first()
            assert entry is not None
            before = compute_clv(s, entry=entry, kickoff_utc=KICK,
                                 policy=policy, data_mode=DATA_MODE)
            _flip_every_flag(s)
            after = compute_clv(s, entry=entry, kickoff_utc=KICK,
                                policy=policy, data_mode=DATA_MODE)
        assert (after.line_clv, after.probability_clv, after.closing_line) == (
            before.line_clv, before.probability_clv, before.closing_line)

    def test_chain_reconciliation_is_unchanged(self, chain) -> None:
        from chainkit import DATA_MODE, GAME, POLICY

        with chain.session() as s:
            before = reconcile_chain(s, canonical_game_id=GAME,
                                     data_mode=DATA_MODE.value,
                                     policy_version=POLICY)["verdict"]
            _flip_every_flag(s)
            after = reconcile_chain(s, canonical_game_id=GAME,
                                    data_mode=DATA_MODE.value,
                                    policy_version=POLICY)["verdict"]
        assert after == before

    def test_forward_performance_rows_are_unchanged(self, chain) -> None:
        with chain.session() as s:
            before = sorted(
                (e.id, e.status, e.settled_at is not None, e.filled)
                for e in s.scalars(select(ForwardLedgerEntry))
            )
            _flip_every_flag(s)
            after = sorted(
                (e.id, e.status, e.settled_at is not None, e.filled)
                for e in s.scalars(select(ForwardLedgerEntry))
            )
        assert after == before


class TestTheClassifiedReadersStayWithinTheirRemit:
    def test_consensus_isolation_does_not_choose_a_close(self, chain) -> None:
        """`latest_consensus_at` excludes flagged rows. That may hide a
        pre-migration row from ordinary consensus lookups; it may not decide
        which snapshot is the close - the capture does that."""
        from chainkit import COHORT, DATA_MODE, GAME, KICK
        from fde_api.forward.consensus import latest_consensus_at

        with chain.session() as s:
            close_before = closing_snapshot_for(
                s, canonical_game_id=GAME, market="SPREAD", cohort=COHORT)
            _flip_every_flag(s)
            # Every snapshot is now flagged, so the compatibility filter
            # hides all of them from the ordinary lookup...
            hidden = latest_consensus_at(
                s, canonical_game_id=GAME, market="SPREAD", as_of_at=KICK,
                data_mode=DATA_MODE)
            # ...and the close is still exactly what the capture says.
            close_after = closing_snapshot_for(
                s, canonical_game_id=GAME, market="SPREAD", cohort=COHORT)
        assert hidden is None, "the compatibility filter did not isolate"
        assert (close_after.id if close_after else None) == (
            close_before.id if close_before else None)

    def test_the_chain_census_counts_captures_not_flags(self, chain) -> None:
        """chain.py counts closing captures, not flagged snapshots, so
        flipping the flag must not move the census either."""
        from chainkit import DATA_MODE, GAME, POLICY
        from fde_api.forward.chain import Stage, read_chain

        with chain.session() as s:
            before = read_chain(s, canonical_game_id=GAME,
                                data_mode=DATA_MODE.value,
                                policy_version=POLICY).stages[Stage.CLOSING_CAPTURE]
            # Scoped to the game: the census is per-game, and the table
            # holds captures for the whole slate.
            captures = len(list(s.scalars(select(ClosingCapture).where(
                ClosingCapture.canonical_game_id == GAME,
                ClosingCapture.data_mode == DATA_MODE.value))))
            _flip_every_flag(s)
            after = read_chain(s, canonical_game_id=GAME,
                               data_mode=DATA_MODE.value,
                               policy_version=POLICY).stages[Stage.CLOSING_CAPTURE]
        assert after == before == captures

    def test_no_current_service_writes_the_flag(self, chain) -> None:
        """The no-write audit, behaviourally: run the chain again and confirm
        every snapshot it wrote left the flag False."""
        from chainkit import SchedulerChain, drive

        restarted = SchedulerChain(chain.factory)
        drive(restarted, with_injuries=False)
        with chain.session() as s:
            flags = {c.is_closing_capture for c in s.scalars(select(ConsensusSnapshot))}
        assert flags <= {False}, "a current service set the legacy flag"
