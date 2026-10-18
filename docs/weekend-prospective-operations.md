# Bounded weekend prospective operation

Research only; NO BET. Use a genuinely new workspace initialized by
`scripts/init_research_workspace.py --database-file <new-path> --prospective`.
Do not adopt an existing acceptance database, copy a protected study, edit a
workspace marker, or change an immutable purpose. Keep the original application
and its read-only workspace available separately.

The standard worker uses the existing nflverse and NWS collectors. Its schedule
comes from the licensed nflverse-data release:
https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv .
Retain nflverse attribution, the CC BY 4.0 license reference, actual receipt time,
raw hashes and normalization provenance. The source can change; a newly retrieved
historical file is training evidence first observed now, not an old forecast.

ESPN automatic collection in the worker is disabled pending documented written
authorization for automated extraction, storage and modeling. Disney's current
terms restrict automated extraction and database compilation without express
written permission: https://disneytermsofuse.com/english/ (section 2.B.x).
Public API accessibility is not permission. The offline parser and retained ESPN
history remain intact. There is no runtime enabling flag. The standalone legacy
ESPN capture script is not part of the permitted weekend procedure and must not
be invoked without established access rights. NFL.com collection remains disabled
under docs/release1b4-official-access.md.

Run the supported worker from apps/api with explicit database, `--prospective`,
`--season 2026`, a maximum of 96 cycles and an interval of at least 300 seconds.
Do not override the early/72h/24h/final policy, fabricate historical execution
times, or bypass the exclusive workspace lock. Only the most recent due horizon
is attempted; report earlier missed horizons. Complete official availability is
still required at the final checkpoint.

The selected-game forecast panel also exposes the existing checkpoint policy as
read-only status: recorded approaches, windows not yet due, missed windows,
withheld publication with its reason, and eligible windows awaiting capture.
This display never generates forecasts or backfills a missed window. Existing
forecast/evaluation labels continue to distinguish pregame, pending final,
evaluated, disputed and historical evidence.

Capture jobs, provider polls, forecast jobs and immutable forecast freezes are
the execution evidence. A process alive or one completed cycle is not proof of
unattended weekend operation. Record observed cycles, interruption/restart
tests, provider errors, misses and the exact remaining observation window.

Do not remove a lock while a worker can still be active. After an interruption,
inspect actual process command lines and confirm the prior worker has stopped
before explicitly recovering its stale lock. Re-authorize the same file identity
and marker before restarting. Never create a second worker for the same database.

Future outcomes remain PENDING. Existing evaluation metrics use uncontested
public-provider finals and retain disagreements; they are not licensed settlement
verification. Independently reconciled results must be distinguished from a lone
provider's result. If independent permitted evidence is unavailable, record that
gap and do not claim independent verification. A game and three model predictions
are one independent outcome, and checkpoints are separate cohorts.

No service, scheduled task, deployment, installed update, provider subscription,
model promotion or betting recommendation is authorized by these instructions.
