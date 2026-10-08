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

Exit 0 prints a JSON status with the absolute `output`, `worksheet_count` (the
number of `worksheets` entries, one per routine) and `identifier_count`. If a temporary file cannot be removed after a successful
save, `warnings` names it; the saved JSON is complete. Failures return exit 1 and
JSON on stderr; invalid CLI flags return argparse's usage message and exit 2. The
directory can be installed anywhere and run on its own.

## Supported inputs

The supported grammar covers the observed PC 10.2.3 Commercial Property, Personal
Auto and Homeowners worksheet shapes, plus the shapes the same release writes
for several routines per worksheet, conditionals and loops: `Worksheets`,
`Worksheet`, `Routine`, `Store`, `PropertySet`, `Variable`, `PropertyGet`,
`Function`, `Argument`, `RateQuery` (`Type` `SingleFactor` or `MultiFactor`),
`QueryParam`, `InstanceMethod`, `Constant`, `ConditionalGroup`, `If`, `ElseIf`,
`Else`, `Condition`, `EndIf`, `Loop` and `Iteration`. Recorded children are
processed in order; nested expression inputs precede their enclosing result. The
optional worksheet `Tag` is retained exactly as supplied. It can distinguish
multiple calculations attached to the same subject and effective dates.

**Routines.** A worksheet holds one or more routines, and each routine becomes
its own entry. Two routines in one worksheet with the same `RoutineCode` (or both
without one) fail, naming both.

**Conditionals.** `If`, `ElseIf`, `Else` and `EndIf` may appear wherever a
statement may: in a routine, a branch or a loop. A chain is `If`, any `ElseIf`s,
at most one `Else`, then `EndIf`. `If` and `ElseIf` start with exactly one
`Condition`; `Else` has none. At most one branch ran (`Result="true"`), and an
`Else` ran exactly when no earlier branch did. A branch that did not run holds
only its condition. The branch that ran gives rows for its condition and its
statements. Reads in conditions that came out false also give rows: PC
evaluated them to choose the branch.

`ConditionalGroup` is layout only. PC opens one at an `If` followed by
`ElseIf` or `Else` and never closes it, so the later statements, chains, loop
markers and groups of the same routine, branch or loop are written inside it.
The group's children are read as if they stood in its place. An empty group
fails.

**Loops.** A `Loop` may appear wherever a statement may, including inside a
branch or another loop. Its first child is the evaluated iterable `Variable`.
After it come the passes, each an `Iteration` marker followed by that pass's
steps; a marker may sit inside a group opened in the pass before it. Checks:

- `IterableSize` is a non-negative integer equal to the number of markers.
- Each marker's `IterationCount` is its 0-based position, and its `Iterable`,
  `IterableType`, `LoopIndexVariable`, `LoopVariable` and `LoopVariableType`
  equal the loop's.
- No step comes before the first marker, and a conditional chain opens and
  closes within one pass.
- `Iteration` appears only in a loop and has no children.

The iterable `Variable`, the markers, and a `Store` of the loop's index or element
variable directly in a pass give no rows. Every other step gives rows as anywhere
else; across passes the last value wins.

Other releases or extensions are supported only if they use this same grammar.
Unsupported tags, attributes, text payloads, or nesting cause explicit failure.
In particular, steps outside a routine, `MultiFactorVariable` queries,
`GetIthElement`, nested function bodies, object-bound functions, extra timestamp
attributes, `Return`, `StaticMethod`, `New`, `Not`, `Note`, and `DisplayHint` are
outside this version. XML comments and formatting whitespace do not affect
values. DTD/entity declarations are rejected. No input expression is evaluated;
only recorded values are used.

## JSON contract (version 3)

The document has `format: "pc-worksheet-final-values"`, `version: 3`, and a
`worksheets` array with one entry per routine, in input order. Each entry
contains:

- `metadata`: its worksheet's supplied `FixedId`, `Description`, `EffectiveDate`,
  `ExpirationDate`, and optional `Tag` attributes, without rewriting or guessing
  missing attributes. Missing and empty tags remain distinct. These are source
  references and labels, not a guaranteed cross-run business key.
- `routine`: the routine's supplied rate-book code/edition and routine
  code/version.
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

Entries are independent scopes, including two routines of one worksheet and
entries with identical metadata. At least one worksheet is required, each with at
least one routine. An empty routine is allowed and produces an empty
`identifiers` object, `{}`. Worksheet and
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

Normalize both sides of a comparison with the same version of this tool. Another
version can write a different set of rows for the same XML, and the comparison
would report those rows as added or removed identifiers.

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
| Call in a condition, when kept | bare call | `isEligible()`, `isEligible().minimumAge` |
| Name part with whitespace or punctuation | the part in single quotes | `'ratinginfo.Structure.Limit'`, `costdata.'Structure.CovType'` |

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
- An argument whose only operand is a `Variable`, `PropertyGet` or `RateQuery`
  has no row: that operand records the same value under its own name. An
  argument built from a constant, a call or several operands keeps its row.
- A call in a condition has no assignment target. When every argument it has
  falls under the rule above, it has no result row either. A call with no
  arguments, or with any other argument, keeps its row.
- A query keeps its table and factor name wherever it appears, including inside
  call arguments and `if` conditions. `MultiFactor` queries are named like
  `SingleFactor` ones.
- Variables and properties are routine state: a read inside a call or condition
  uses the same name as the assignment (`costdata.BaseRate`).
- The object type and the function's class are not part of a name. When two
  classes share a function name in one routine, the call carries the simple
  class name: `CalcRoutineRoundingMethod.setScale()`.
- A name part (variable, object, property, function, class, argument, method,
  table or query parameter) holding whitespace or any of `. [ ] ( ) " ' : = \`
  is written in single quotes, with `\` as `\\` and `'` as `\'`. Factor names
  and sources are always quoted, escaped the same way:
  `'Company Deviation Factor_Ext'['Company Deviation Factor'].'PERP Indicator'`.
  A dotted variable and an object property with the same text stay two names:
  `'ratinginfo.Structure.CauseOfLoss'` and `ratinginfo.'Structure.CauseOfLoss'`.
- Every name keeps its last value in recorded order; nested inputs precede the
  result they feed. A call, argument or query recorded again replaces its
  earlier value, and no position suffix is added.
- Operators, parentheses, declaration flags, display categories and branch
  results are not identifiers. A property read's recorded `ObjectValue` is not
  kept.

Conversion fails with `unsupported_content`, naming the element, when:

- a name part is empty;
- one object name is recorded with two object types in one routine;
- two classes with the same simple name share a function name in one routine;
- a routine, conditional chain or loop breaks the rules in
  [Supported inputs](#supported-inputs).

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
