# Refreshing city validation and calibration

Current city calibration state and validation summaries are derived answers.
Their writers replace the previous answer on their natural key. Otherwise a
successful repeat run would discard its new answer and leave the first one
in place. Immutable prediction and outcome records retain insert-only writes.

The integration test runs learning/scoring twice, then scoring alone through
the real HTTP write helper with duplicate handling. It checks that a pending
call becomes a scored miss, fitting dates advance, score-only work does not
refresh calibration dates, and the original prediction remains unchanged.
Nightly fit reports remain versioned evidence in the repository.
