# Worksheet normalization examples

Run these with the [normalization bundle](../../../skills/pc-worksheet-normalization/README.md).
All expected JSON files follow its version 2 contract.

| Input | Expected output | Coverage |
|---|---|---|
| [cp-synthetic.xml](cp-synthetic.xml) | [cp-synthetic.expected.json](cp-synthetic.expected.json) | Nested assignment, later property read, final missing value, numeric-looking string. |
| [pa-synthetic.xml](pa-synthetic.xml) | [pa-synthetic.expected.json](pa-synthetic.expected.json) | Entity-valued driver assignment, named function inputs, query, missing prior term amount, conditional and void method. |
| [homeowners-synthetic.xml](homeowners-synthetic.xml) | [homeowners-synthetic.expected.json](homeowners-synthetic.expected.json) | Line object, function return, dwelling property, lookup and untyped query parameters. |
| [Comparison illustrations](comparison/README.md) | Three before/after normalized pairs | Reordering, repeated descriptions, slices, tags, changed IDs, unchanged finals and an explicit context insufficiency case. |

The examples use invented identifiers, labels and objects. They model observed
element shapes without publishing real worksheets or personal details. Their
complete expected records are independently specified in
[expected_fixtures.py](../../../tests/worksheet-normalize/expected_fixtures.py),
which does not parse XML or call the converter.

From the repository root, using a fresh destination:

```sh
python3 skills/pc-worksheet-normalization/scripts/worksheet-normalize.py \
  --input examples/worksheets/normalized/cp-synthetic.xml \
  --output /tmp/comparable-cp.json
python3 tests/worksheet-normalize/test_normalize.py -v
```

The tests cover repeated occurrences (`10,20` versus `15,20` and `10,21`),
precision, nulls, every named category, independent worksheet/object/call/query
contexts, unsupported semantics, malformed/unreadable inputs, write failures,
explicit overwrite, input fidelity and isolated bundle execution.
