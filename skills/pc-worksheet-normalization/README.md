# Worksheet normalization

Convert a local PC `Worksheets` XML document to JSON containing each
named identifier's final recorded value. Python 3.8+ standard library is the
only runtime requirement. There is no database access or dependency installation.

## Run

```sh
python3 /path/to/pc-worksheet-normalization/scripts/worksheet-normalize.py \
  --input /path/to/worksheets.xml
```

Without `--output`, the JSON is written to `worksheets-normalized.json` in the
input XML's folder. `--output /path/to/file.json` writes it elsewhere; the
destination's parent directory must exist. Existing files are preserved, so a
second XML in the same folder needs its own `--output` or `--overwrite`;
add `--overwrite` to explicitly replace a destination. The input cannot be the
destination, including symlink or hard-link aliases, even with overwrite. Output
is written to a temporary file beside the destination, then published atomically.
A filesystem must support hard links for the default no-clobber publication.

Exit 0 prints a JSON status with the absolute `output`, `worksheet_count` and
`identifier_count`. If a temporary file cannot be removed after a successful
save, `warnings` names it; the saved JSON is complete. Failures return exit 1 and
JSON on stderr; invalid CLI flags return argparse's usage message and exit 2. The
directory can be installed anywhere and run on its own.

## Supported inputs

The supported grammar covers the observed PC 10.2.3 Commercial Property, Personal
Auto and Homeowners worksheet shapes: `Worksheets`, `Worksheet`, `Routine`,
`Store`, `PropertySet`, `Variable`, `PropertyGet`, `Function`, `Argument`,
`RateQuery`, `QueryParam`, `InstanceMethod`, `Constant`, `ConditionalGroup`, `If`,
`Condition`, `Else`, and `EndIf`. Each worksheet has one routine. Recorded children
are processed in order; nested expression inputs precede their enclosing result.
An unexecuted branch may contain its evaluated condition but no statements. An
`If` and its `Else` must record different results; equal results are rejected.
The optional worksheet `Tag` is retained exactly as supplied. It can distinguish
multiple calculations attached to the same subject and effective dates.

Other releases or extensions are supported only if they use this same grammar.
Unsupported tags, attributes, text payloads, or nesting cause explicit failure.
In particular, loops, nested function bodies, object-bound functions, multi-factor
queries, extra timestamp attributes, `Return`, `StaticMethod`, `New`, `Not`,
`ElseIf`, `Note`, and `DisplayHint` are outside this version. XML comments and
formatting whitespace do not affect values. DTD/entity declarations are rejected.
No input expression is evaluated; only recorded values are used.

## JSON contract (version 3)

The document has `format: "pc-worksheet-final-values"`, `version: 3`, and a
`worksheets` array in input order. Each worksheet contains:

- `metadata`: its supplied `FixedId`, `Description`, `EffectiveDate`,
  `ExpirationDate`, and optional `Tag` attributes, without rewriting or guessing
  missing attributes. Missing and empty tags remain distinct. These are source
  references and labels, not a guaranteed cross-run business key.
- `routine`: the supplied rate-book code/edition and routine code/version.
- `identifiers`: an object whose keys are the identifier names and whose values
  are their final values, written with keys in Unicode code-point order (so
  uppercase letters sort before `_`, and `_` before lowercase letters):

  ```json
  "identifiers": {
    "basisFactor": "0.01",
    "costdata.TermAmount": "992",
    "cp_deduct['Factor']": "0.95",
    "cp_deduct['Factor'].JURISDICTION": "CA"
  }
  ```

Worksheet array entries are independent scopes even when metadata is identical.
At least one worksheet is required, with exactly one routine per worksheet. An
empty routine is allowed and produces an empty `identifiers` object, `{}`. Worksheet and
routine attributes are optional: absent or empty FixedId, dates and routine
codes remain as recorded. The converter does not validate qualified FixedId syntax,
date chronology or worksheet-identity uniqueness. Required identifier names and
call/query shape attributes must be nonempty. Conversion preserves available
context; it does not certify that every worksheet can be matched.

There are no event indexes, input hashes, file paths, run timestamps, or interim
values in the output. Metadata is retained for downstream policy decisions; this
tool does not match worksheets across files.

### What the output can and cannot identify

`FixedId` names the source subject (often a cost, sometimes a vehicle or
dwelling); the dates give its interval; `Tag` can tell apart two calculations on
the same subject. Routine and book fields describe the calculation used, and
their versions can change between runs. Description, array position, final
amounts and object display text are not business keys. Absent and empty `Tag`
differ. Attribute text follows normal XML decoding, with no trimming or case
folding.

The XML does not consistently carry building, vehicle or dwelling keys, coverage
codes or links from a cost to its subject. Normalization does not invent them,
and a successful conversion does not mean every worksheet can be paired with
another run. A whole-file text diff of two outputs is not a comparison; use the
comparison bundle.

### Names

Each name is a short expression whose syntax shows what the value is and where
it sits in the routine:

| What | Name | Example |
|---|---|---|
| Variable | its name | `basisFactor` |
| Property | object name `.` property | `costdata.TermAmount` |
| Rate query result | table `['factor']`, with `, 'source'` when the factor source is nonempty | `cp_deduct['Factor']`, `t['Factor', 'override']` |
| Query parameter | query name `.` parameter | `cp_deduct['Factor'].JURISDICTION` |
| Function argument | assignment target `:=` call `.` argument | `costdata.AdjustedRate := min().num1` |
| Void method argument | object `.` method `()` `.` argument | `a.m().Value` |
| Function result, when kept | assignment target `:=` call | `costdata.AdjustedRate := min()` |
| Call nested in an argument | the argument's name `.` call | `x := outer().right.inner()` |
| Call inside a rounding call, when kept | assignment target `:=` call | `defaultCashLimit := getCovCDefaultLimitPercentage()` |
| Call in a condition | bare call | `isEligible()`, `isEligible().vehicle` |

Rules:

- Rounding calls have no argument rows. PC marks them with a `Type`
  attribute: a `Function` with `Type="Rounding"` (`setScale`: `Value`, `Scale`,
  `Mode`) and an `InstanceMethod` with `Type="CostDataRounding"`
  (`_costdata.setRounding`: `Scale`, `Mode`). The rounded result stays on its
  target (`costdata.TermAmount`). Operands inside a rounding argument are named
  as operands of the enclosing assignment: reads keep their own names, and a
  call is named by the target without the rounding step. A rounding call that is
  one operand among several keeps its result row (`total := setScale()`). The
  unrounded input, mode and scale are not kept, so a change in rounding settings
  shows only when the rounded amount moves. A call without one of these `Type`
  values is an ordinary call, whatever its name.
- A call is named by the target its result is assigned to, so the same call in
  two assignments keeps separate arguments. When a call is the only operand of an
  assignment or argument, it has no result row: the target or argument already
  holds that value. A call that is one operand among several keeps its result
  row under the target. Void methods have no result row.
- A query keeps its table and factor name wherever it appears, including inside
  call arguments and `if` conditions.
- Variables and properties are worksheet state: a read inside a call or condition
  uses the same name as the assignment (`costdata.BaseRate`).
- The object type and the function's class are not part of a name. When two
  classes share a function name in one worksheet, the call carries the simple
  class name: `CalcRoutineRoundingMethod.setScale()`.
- A variable or property written or read again keeps its last value in recorded
  order; nested inputs precede the result they feed.
- Operators, parentheses, declaration flags, display categories and branch
  results are not identifiers. A property read's recorded `ObjectValue` is not
  kept.

Conversion fails with `unsupported_content`, naming the element, when a name
cannot be read back unambiguously:

- a variable, object, property, function, class, argument, parameter, method or
  table name is empty or contains whitespace or any of `. [ ] ( ) " : =`;
- a factor name or factor source contains `'`, `"` or `\` (spaces and dots are
  allowed inside the quotes);
- a call, argument, query or query parameter name is written twice in one
  worksheet (the message names both elements);
- one object name is recorded with two object types in one worksheet;
- two classes with the same simple name share a function name in one worksheet.

### Values

Each value is plain JSON:

| Recorded value | JSON value |
|---|---|
| Number (Java Byte/Short/Integer/Long/Float/Double, BigInteger/BigDecimal, primitive numeric types) | Canonical exact decimal **string**, never a JSON floating-point number. `1.00` → `"1"`; `0.09918` stays distinct from `0.0992`. |
| String, or no recorded type | The original string, including empty strings and numeric-looking strings such as `001`. Untyped query parameters remain strings. |
| Java Boolean or primitive boolean | JSON `true`/`false`. |
| Value attribute absent | JSON `null`, replacing any prior value. The XML does not distinguish null from unavailable. |
| Entity, array, typekey, enum or other type | The original recorded string; no object fields are invented. |

Decimal and scientific notation are canonicalized without rounding; negative
zero becomes `"0"`. Non-finite or invalid typed numbers fail; expanded values
above 100,000 digits fail explicitly. This counts all rendered digits, including
the leading zero of a fraction, but excludes the sign and decimal point. A
literal string `"null"` is not a missing value.

No recorded type is kept. A number and a string with the same text are the same
value (`"20"`), and identical numbers recorded with different Java types are
equal.

## Failures

| Category | Remedy |
|---|---|
| `input_read` | Check the input path, home-directory expansion, symlink targets, file type, and read permissions. |
| `malformed_xml` | Supply a complete, well-formed XML export. |
| `unsupported_content` | Inspect the reported element/attribute or shape; use a supported export or extend and test the converter. Do not delete data to bypass validation. |
| `input_output_same` | Choose a different destination. |
| `output_exists` | Choose a fresh destination or explicitly request `--overwrite`. |
| `output_write` | Check destination path/home expansion, symlink targets, parent directory, permissions, space, and filesystem support. |

Validation finishes before output publication. Failed conversion preserves any
existing destination, including when `--overwrite` was requested.
