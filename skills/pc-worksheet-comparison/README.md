# Worksheet comparison

Compare two retained PC rating captures of the same job and quote
branch. Inputs are `pc-worksheet-final-values` version 3 JSON from the
normalization bundle. The run writes comparison JSON and, with `--report`, a
Markdown report for rating analysts and PC developers. Python 3.8+
standard library only; copy the bundle anywhere and run it from any directory.

```sh
python3 /path/to/pc-worksheet-comparison/scripts/worksheet-compare.py \
  --baseline /path/to/baseline.json --candidate /path/to/candidate.json \
  --output /path/to/comparison.json --report /path/to/report.md
```

## Status and files

| Exit | Outcome | Files |
|---|---|---|
| 0 | `equal` or `different` | Comparison JSON and report published. Changes are findings, not failures. |
| 3 | `incomplete` | Both published; they hold the established pairs and every unresolved worksheet with reasons. |
| 2 | Error | Neither file is published; prior files are untouched. Usage errors also exit 2. |

Stdout is a JSON status with `status`, `output`, `report` and `summary`. Errors
print JSON with `status`, `category` and `message` on stderr. Categories:
`invalid_input`, `unsupported_version`, `input_read`, `path_error`,
`input_output_same`, `output_exists`, `output_write`.

- Inputs are never written. An output or report path that is an input, the other
  output, or an alias of either (symlink, hard link, parent-directory link) is
  refused, even with `--overwrite`. Comparing a file with itself is allowed.
- An existing output or report is replaced only with `--overwrite`; a symlink is
  then replaced itself, never its target.
- Each file is written to a temporary file beside it, flushed, then published
  atomically. The two files are published together: if the second fails, the
  first is put back as it was. Default mode needs hard-link support.
- A temporary file that cannot be removed after success is named in `warnings`.
- Both files contain no timestamps or absolute paths. The same inputs give the
  same bytes.

## Valid inputs

The root has exactly `format`, integer `version: 3` and a nonempty `worksheets`
array. Any other version, mixed versions and other envelopes fail with
`unsupported_version`; re-normalize an older capture from its retained worksheet
XML. Unknown fields, duplicate JSON keys (so a name written twice in one
entry), non-JSON numbers, invalid UTF-8 or surrogates, and invalid shapes
fail with `invalid_input`. Nothing is dropped or repaired. Worksheet and
identifier order do not count as changes.

Each `worksheets` entry is one routine of one PC worksheet; a worksheet
with several routines gives several entries with the same `metadata`. An entry
has `metadata` (optional text `FixedId`, `Tag`, `EffectiveDate`,
`ExpirationDate`, `Description`), `routine` (optional text `RateBookCode`,
`RateBookEdition`, `RoutineCode`, `RoutineVersion`) and `identifiers`. Missing
metadata is valid but can prevent pairing. `identifiers` is an object of name
to value: each key is a nonempty name written by the normalization grammar
(`cp_deduct['Factor']`), and each value is a string, a boolean or null. An
`identifiers` array of `{name, value}` records fails with `invalid_input`;
re-normalize that capture from its retained worksheet XML. Numbers arrive as
canonical decimal strings. Null is different from an
absent identifier. No float conversion, tolerance, coercion or object
reconstruction is done.

## Pairing worksheets

Two worksheets pair only when each is the other's single exact match on all five
fields: qualified `FixedId` (one colon, nonblank parts), Tag presence and value,
raw `EffectiveDate`, raw `ExpirationDate`, and nonempty `RoutineCode`. Text is
compared as recorded: no trimming, date parsing or case folding. Absent Tag and
empty Tag differ.

A missing, empty or unqualified field is unknown. Worksheets compete when every
field known on both sides agrees. A worksheet with an unknown field never pairs,
and it blocks any pair it competes with; known conflicting fields keep unrelated
pairs intact. Unpaired worksheets are reported as unresolved with their possible
partners. They are not treated as additions, removals, splits or merges.

Within a pair, identifiers match on their exact name. Description, rate book and
routine version are compared after pairing.

## Comparison JSON (version 3)

Root: `format: pc-worksheet-comparison`, `version: 3`, `policy`, `outcome`,
`complete`, `inputs`, `summary`, `pairs`, `unresolved`, `limits`. It holds
changes, not copies of unchanged identifiers; those stay in the inputs.

- `inputs.baseline` / `inputs.candidate`: SHA-256 of the input bytes, format,
  version, worksheet and identifier counts.
- `pairs[]`: one entry per established pair, in baseline order, with
  `baseline` and `candidate` (`index`, `metadata`, `routine`) and `outcome`.
  A `different` pair adds:
  - `context_changes[]`: `group` (`metadata` or `routine`), `field`, and
    `baseline`/`candidate` as `{present: false}` or `{present: true, value}`.
  - `identifier_changes[]`: `name`, `categories`, and `baseline`/`candidate`
    as `{value}`, or `null` when the identifier is absent on that side.
- `unresolved[]`: `side`, `worksheet` (`index`, `metadata`, `routine`),
  `reasons`, `unavailable_identity_fields`, `possible_partner_indexes`.

Each changed identifier has one category:

- `value`: the JSON value changed. Strings compare exactly, so `"0.0992"` and
  `"0.09918"` differ; a string and a boolean or null differ. A number and a
  string with the same text are equal, as are numbers recorded with different
  Java types.
- `added` / `removed`: the identifier is present on one side only.

`summary` counts `established_pairs`, `changed_pairs`, `compared_identifiers`
(union within pairs), `changed_identifiers` (each once), `changed_context_fields`,
`identifier_categories`, and `unresolved_baseline` / `unresolved_candidate`.
Any unresolved worksheet makes the outcome `incomplete` (exit 3), even if every
pair is equal. Otherwise any change makes it `different`, and none makes it
`equal`. The inputs cannot prove they come from the same job and branch; that is
the caller's assumption, and `limits` repeats it.

## Report

In order: the outcome and counts; one table per changed pair (worksheet,
identifier, before, after, category), with context changes as `context` rows;
one line per equal pair; unresolved worksheets with reasons; and an appendix
holding the exact comparison JSON. Indexes are zero-based positions in each input.

Source text is shown in code spans with JSON escapes, so Markdown, HTML, control
and direction characters in values cannot change the report. Identifiers are
shown by name and values as JSON (`"992"`, `true`, `null`). A cell longer than
60 characters is cut and marked `(shortened)`. The report infers
no premium totals, business impact or object changes.
