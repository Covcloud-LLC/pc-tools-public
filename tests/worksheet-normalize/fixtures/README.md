# Normalization test fixtures

Invented worksheets that exercise the normalization grammar. Each XML has its
expected output beside it; [expected_fixtures.py](../expected_fixtures.py) writes
those records out by hand, without parsing XML or calling the converter.

| Input | Expected output | Covers |
|---|---|---|
| [cp-synthetic.xml](cp-synthetic.xml) | [cp-synthetic.expected.json](cp-synthetic.expected.json) | Nested assignment, later property read, final missing value, numeric-looking string. |
| [pa-synthetic.xml](pa-synthetic.xml) | [pa-synthetic.expected.json](pa-synthetic.expected.json) | Entity-valued driver assignment, named function inputs, query, missing prior term amount, conditional and void method. |
| [homeowners-synthetic.xml](homeowners-synthetic.xml) | [homeowners-synthetic.expected.json](homeowners-synthetic.expected.json) | Line object, function return, dwelling property, lookup and untyped query parameters. |
