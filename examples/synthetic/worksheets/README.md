# Synthetic worksheet examples

Three invented baseline/candidate pairs, run through the worksheet pipeline. Each
case folder holds the normalization input and output for both sides
(`baseline.xml` → `baseline.json`, `candidate.xml` → `candidate.json`) and the
comparison output (`comparison.json`, `report.md`). The data is invented; the
`finalAmount` variable is made up for clarity, and the worksheet and routine
shapes follow Commercial Property, Personal Auto and Homeowners.

| Case | What it shows | Comparison result |
|---|---|---|
| [cp](cp/report.md) | Worksheets reordered; book edition 1 → 2 on all three; the first slice of cost `a` changes `finalAmount` 20 → 21. | `different`: 3 pairs, 1 changed identifier, 3 context changes |
| [pa](pa/report.md) | Same vehicle, dates and description on two worksheets told apart only by `Tag`; `driver:a` changes 10 → 11. | `different`: 2 pairs, 1 changed identifier |
| [homeowners](homeowners/report.md) | Two dwelling costs share description, interval, routine and amount; their references change from `a`, `b` to `x`, `y`. Nothing records which is which. | `incomplete`: 0 pairs, 4 unresolved worksheets |

Regenerate a case from the repository root (use a fresh output path, or add
`--overwrite`):

```sh
python3 skills/pc-worksheet-normalization/scripts/worksheet-normalize.py \
  --input examples/synthetic/worksheets/pa/baseline.xml --output /tmp/baseline.json
python3 skills/pc-worksheet-comparison/scripts/worksheet-compare.py \
  --baseline examples/synthetic/worksheets/pa/baseline.json \
  --candidate examples/synthetic/worksheets/pa/candidate.json \
  --output /tmp/comparison.json --report /tmp/report.md
```

The expected JSON for each side is written out by hand in
[expected_fixtures.py](../../../tests/worksheet-normalize/expected_fixtures.py).
The [normalization tests](../../../tests/worksheet-normalize/test_normalize.py)
check the XML converts to it, and the
[comparison tests](../../../tests/worksheet-compare/test_report.py) rebuild each
`comparison.json` and `report.md` and compare them by hash.
