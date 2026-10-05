# Worksheet comparison

Compare two retained PC rating captures of the same job and quote
branch. Inputs are `pc-worksheet-final-values` version 2 JSON from the
normalization bundle. The run writes comparison JSON and, with `--report`, a
Markdown report for rating analysts and PC developers. Python 3.8+
standard library only; copy the bundle anywhere and run it from any directory.

```sh
python3 /path/to/pc-worksheet-comparison/scripts/worksheet-compare.py \
  --baseline /path/to/baseline.json --candidate /path/to/candidate.json \
  --output /path/to/comparison.json --report /path/to/report.md
```

Examples: [synthetic cases](../../examples/synthetic/worksheets/README.md), each with its `comparison.json` and `report.md`.

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

The root has exactly `format`, integer `version: 2` and a nonempty `worksheets`
array. Version 1, mixed versions and other envelopes fail. Unknown fields,
duplicate JSON keys, duplicate identifiers in a worksheet, non-JSON numbers,
invalid UTF-8 or surrogates, and invalid shapes fail. Nothing is dropped or
repaired. Worksheet and identifier order do not count as changes.

Each worksheet has `metadata` (optional text `FixedId`, `Tag`, `EffectiveDate`,
`ExpirationDate`, `Description`), `routine` (optional text `RateBookCode`,
`RateBookEdition`, `RoutineCode`, `RoutineVersion`) and `identifiers`. Missing
metadata is valid but can prevent pairing. Identifiers follow the normalization
grammar (variable, property, function, query, argument, parameter with structured
context). Values have `kind`, `type` and `value`:

| Kind | Value |
|---|---|
| `number` | Canonical decimal string for a numeric type; no exponent, sign `+`, leading zeros, `-0` or trailing fractional zeros; at most 100,000 digits. |
| `boolean` | JSON boolean with a boolean type. |
| `string` | Text with no type or a String/Character/char type. |
| `null` | JSON null with any recorded type. Different from an absent identifier. |
| `opaque` | Text with a non-scalar type and `opaque: true`. |

A property may carry `receiver: {type, value, opaque: true}`, whose `type` equals
the property's object type. No float conversion, tolerance, coercion or object
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

Within a pair, identifiers match on their full structured identity. Description,
rate book and routine version are compared after pairing.

## Comparison JSON (version 2)

Root: `format: pc-worksheet-comparison`, `version: 2`, `policy`, `outcome`,
`complete`, `inputs`, `summary`, `pairs`, `unresolved`, `limits`. It holds
changes, not copies of unchanged identifiers; those stay in the inputs.

- `inputs.baseline` / `inputs.candidate`: SHA-256 of the input bytes, format,
  version, worksheet and identifier counts.
- `pairs[]`: one entry per established pair, in baseline order, with
  `baseline` and `candidate` (`index`, `metadata`, `routine`) and `outcome`.
  A `different` pair adds:
  - `context_changes[]`: `group` (`metadata` or `routine`), `field`, and
    `baseline`/`candidate` as `{present: false}` or `{present: true, value}`.
  - `identifier_changes[]`: `identifier`, `categories`, and `baseline`/`candidate`
    as `{value, receiver?}`, or `null` when the identifier is absent on that side.
- `unresolved[]`: `side`, `worksheet` (`index`, `metadata`, `routine`),
  `reasons`, `unavailable_identity_fields`, `possible_partner_indexes`.

Identifier categories (one identifier can count in several):

- `value`: kind or recorded value changed, including opaque text.
- `type`: kind or recorded type changed.
- `receiver`: receiver presence or text changed.
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
shown in a short readable form; the appendix holds the structured identity. A
cell longer than 60 characters is cut and marked `(shortened)`. The report infers
no premium totals, business impact or object changes.
