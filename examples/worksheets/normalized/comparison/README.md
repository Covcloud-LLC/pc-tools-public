# Normalized comparison illustrations

These small synthetic pairs use the normalization bundle's version 2 contract.
They isolate context questions; they are not complete PC rating traces. No real
personal data is included. The `finalAmount` variable is invented for clarity.
Source shapes and routine names follow Commercial Property, Personal Auto and
Homeowners worksheets; repeated subjects are deliberate synthetic stress cases,
not claims about real jobs or configured product limits.

| Pair | Before JSON | After JSON | Independently specified meaning |
|---|---|---|---|
| CP | [before](cp-before.expected.json) | [after](cp-after.expected.json) | Same cost references and slices; worksheets reordered; book edition 1 → 2. Before indexes 0, 1, 2 correspond to after indexes 2, 0, 1. Only the first slice of cost `a` changes final amount, 20 → 21. Cost `b` remains 40; second slice of `a` remains 20 despite interim and numeric-format changes. |
| PA | [before](pa-before.expected.json) | [after](pa-after.expected.json) | Same vehicle, dates and identical display descriptions, two driver tags. Before indexes 0, 1 correspond to after indexes 1, 0. `driver:a` changes 10 → 11; `driver:b` remains 20. Tag is necessary to distinguish these contexts. |
| Homeowners | [before](homeowners-before.expected.json) | [after](homeowners-after.expected.json) | Two synthetic dwelling costs share description, interval, routine and final 20. Source IDs change from `a,b` to `x,y`. Fixture-author intent is `a → y`, `b → x`, but no field records that correspondence. `a → x`, `b → y` fits the JSON equally well. Report the information gap; do not infer a match from order or equal amounts. |

Indexes above are zero-based descriptions of the test oracle, not matching keys or
a manual-mapping input. The CP/PA examples stipulate reference continuity for the
fixture; they do not prove production IDs stable between runs. `Tag` is retained
verbatim, not interpreted as a universal driver key. A changed tag raises the same
business-identity question as a changed FixedId.

Input XML is beside each JSON, for example [PA before XML](pa-before.xml). The
comparator needs only normalized JSON; the XML is here to reproduce normalization.
Final expected JSON is independently specified in
[expected_fixtures.py](../../../../tests/worksheet-normalize/expected_fixtures.py).
The [normalization tests](../../../../tests/worksheet-normalize/test_normalize.py)
verify all six full outputs, the stated final values and context. The
[comparison tests](../../../../tests/worksheet-compare/test_compare.py) run the
[comparison bundle](../../../../skills/pc-worksheet-comparison/README.md) on
these pairs.

```sh
python3 skills/pc-worksheet-normalization/scripts/worksheet-normalize.py \
  --input examples/worksheets/normalized/comparison/pa-before.xml \
  --output /tmp/pa-before-normalized.json
python3 tests/worksheet-normalize/test_normalize.py -v
```

Use a fresh output path.
