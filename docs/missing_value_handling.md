# Handling Missing Inputs in Derived Calculations

This document summarizes how the missing-value safeguards that were introduced in
`extractors.py` ensure that derived quantities remain `-1` whenever any of their
source measurements are still unknown.

## Shared helper utilities

* `_is_missing` centralizes the definition of the `-1` sentinel (and equivalent
  placeholder strings). Any value that resolves to the sentinel short-circuits
  the downstream calculation.
* `_coerce_number` relies on `_is_missing` so that numbers are only produced
  when a field has a concrete value. If a field is empty, `None` is returned
  and the caller leaves the result at `-1`.
* `_maybe_set_total_duration` only backfills `total_dur` when *both*
  `enroll_dur` and `subj_dur` are available. Otherwise, the pre-existing
  sentinel `-1` is preserved.

These helpers mean every consumer can gate formulas on the presence of real
numbers instead of repeating ad-hoc checks for the `-1` placeholder.

## DMC calculations (`calculate_dmc`)

* All inputs are converted through `_coerce_number`. If any prerequisite total
  counts or durations are missing, the helper returns `None` and the field is
  left at `-1`.
* The `set_scaled` helper only applies scaling factors when its input is a real
  number. Missing inputs keep their target fields at `-1`.
* Meeting counts fall back to `-1` if both the reported count and the meeting
  frequency are unavailable, or if the subject duration is still unknown. This
  prevents the "negative meeting" artefacts that were caused by math on
  placeholder values.

## Refresh calculations (`calculate_refresh`)

* The subject duration (`subj_dur`) is inspected with `_coerce_number`.
* When the extractor cannot find explicit refresh counts, the code now returns
  `-1` if the subject duration is missing. Default heuristic formulas are only
  triggered when `subj_dur` is a real value.

## Data management totals (`get_data_dm`)

* Each intermediate count (screen failures, completes, withdrawals, etc.) is
  converted via `_coerce_number`. If any dependency is unavailable, the
  aggregate total remains `-1`.
* For example, `crf_pages_total` only sums the three sub-components when *every*
  contributing number is present. Otherwise, it returns `-1` so downstream
  formulas know that the total is still unknown.
* The same pattern is used for `manual_queries_total` and `auto_queries_total`.

Together, these changes ensure that every derived calculation now checks for the
`-1` sentinel before performing arithmetic. Whenever any input is missing, the
result continues to propagate `-1`, which keeps the rendered table blank instead
of displaying misleading negative numbers.
