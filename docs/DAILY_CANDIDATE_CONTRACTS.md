# Candidate validation and incompatible contracts

The Daily Pipeline's shadow-candidate evaluation uses the original recorded
first-call vectors and the exact settled venue contract. Station, unit, bucket
geometry and ordered band identities must agree. A city/date alone cannot join
a forecast to a settlement. An inferred city-default station in an old definition
file is not contract evidence: the original hashed vector's station must match
settlement, and an explicit received rule station must also match.

An incompatible contract is excluded from candidate validation, with its reason
and contract differences in `input_coverage`. The original first-call denominator
is retained. The original vector and settlement remain unchanged. Excluded
calls cannot count as successful predictions or support model promotion.

The candidate plan checks contract compatibility and frozen market completeness
before allowing historical table construction and model fitting. Existing
minimum cities per checkpoint/date and final promotion gates still apply.
If eligible coverage is insufficient, the cycle waits. Final label attachment
also occurs before fitting. Hash/receipt failures, ambiguous venue identities
and malformed market quotes remain errors.

This repairs a failure in the candidate job; it does not update serving model
weights, repair historical forecasts, or replace market settlements. Future
predictions must use the station named in their own received market rules.

Regression coverage is in `tests/test_decision_inputs.py` and
`tests/test_candidate_cycle.py`: station, unit and geometry conflicts cannot
abort other cities, disappear from the denominator, alter frozen vectors, or
reach model fitting unnoticed.
