# Idempotency

PASS: first actual evaluation added 3; second added 0, with identical persisted metrics (execution.json). The actual database has zero duplicate non-null forecast/result keys (preservation.json).

Stable IDs derive from forecast ID plus result observation ID. A unique database index additionally enforces (prediction_id, result_observation_id), so changing the evaluation row's ID cannot duplicate the same pair. The new nullable columns preserve legacy records. New evaluator writes both identity fields.

Controlled test_postgame_integrity.py runs two independent SQLite sessions concurrently against a migrated file database: exactly three rows survive, any losing transaction rolls back, and retry adds zero. A direct insertion with a different row ID and the same logical pair is rejected. Freeze UPDATE and DELETE are rejected by database triggers. Partial evaluation insertion failure rolls back all new evaluations while retaining forecasts, then retry recovers. This is SQLite evidence, not a claim of a PostgreSQL concurrency run.

No destructive migrations ran. Migration adds an empty freeze table and nullable evaluation identity columns plus uniqueness. Populated freeze-table downgrade is refused. Database backup precedes the migration. Historical evaluations remain immutable.
