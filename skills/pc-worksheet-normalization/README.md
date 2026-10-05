# Worksheet normalization

Convert a local PC `Worksheets` XML document to JSON containing each
qualified identifier's final recorded value. Python 3.8+ standard library is the
only runtime requirement. There is no database access or dependency installation.

## Run

```sh
python3 /path/to/pc-worksheet-normalization/scripts/worksheet-normalize.py \
  --input /path/to/worksheets.xml --output /path/to/comparable.json
```

The destination's parent directory must exist. Existing files are preserved;
add `--overwrite` to explicitly replace a destination. The input cannot be the
destination, including symlink or hard-link aliases, even with overwrite. Output
is written to a temporary file beside the destination, then published atomically.
A filesystem must support hard links for the default no-clobber publication.

Exit 0 prints a JSON status with the absolute `output`, `worksheet_count` and
`identifier_count`. If a temporary file cannot be removed after a successful
save, `warnings` names it; the saved JSON is complete. Failures return exit 1 and
JSON on stderr; invalid CLI flags return argparse's usage message and exit 2. The
directory can be installed anywhere and run on its own.

Examples: [synthetic cases](../../examples/synthetic/worksheets/README.md) (`baseline.xml` → `baseline.json`).

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

## JSON contract (version 2)

The document has `format: "pc-worksheet-final-values"`, `version: 2`, and a
`worksheets` array in input order. Each worksheet contains:

- `metadata`: its supplied `FixedId`, `Description`, `EffectiveDate`,
  `ExpirationDate`, and optional `Tag` attributes, without rewriting or guessing
  missing attributes. Missing and empty tags remain distinct. These are source
  references and labels, not a guaranteed cross-run business key.
- `routine`: the supplied rate-book code/edition and routine code/version.
- `identifiers`: one final record per qualified identifier, sorted by the canonical
  JSON of its identity. Each record has `identifier` and `value`.

Worksheet array entries are independent scopes even when metadata is identical.
At least one worksheet is required, with exactly one routine per worksheet. An
empty routine is allowed and produces an empty `identifiers` array. Worksheet and
routine attributes are optional: absent or empty FixedId, dates and routine
codes remain as recorded. The converter does not validate qualified FixedId syntax,
date chronology or worksheet-identity uniqueness. Required identifier names and
call/query shape attributes must be nonempty. Conversion preserves available
context; it does not certify that every worksheet can be matched.

There are no event indexes, input hashes, file paths, run timestamps, or interim
values in the output. Metadata and opaque identities are retained for downstream
policy decisions; this tool does not match worksheets across files.

### What the output can and cannot identify

`FixedId` names the source subject (often a cost, sometimes a vehicle or
dwelling); the dates give its interval; `Tag` can tell apart two calculations on
the same subject. Routine and book fields describe the calculation used, and
their versions can change between runs. Description, array position, final
amounts and opaque display text are not business keys. Absent and empty `Tag`
differ. Attribute text follows normal XML decoding, with no trimming or case
folding.

The XML does not consistently carry building, vehicle or dwelling keys, coverage
codes or links from a cost to its subject. Normalization does not invent them,
and a successful conversion does not mean every worksheet can be paired with
another run. A whole-file text diff of two outputs is not a comparison; use the
comparison bundle.

Identity rules:

| Kind | Identity inside a worksheet |
|---|---|
| `variable` | Exact variable name; reads and stores share an identity. |
| `property` | Exact property name plus object name and object type; reads and writes share an identity. |
| `function` | Class/name plus enclosing call/query/input context. |
| `query` | Table code, factor name, factor source plus enclosing context. Table display name is not identity. |
| `argument` | Exact name plus owning function or void instance-method context. |
| `parameter` | Exact name plus owning query context. |

Contexts are arrays of structured objects, so punctuation in names cannot cause
concatenation collisions. Nested calls include the enclosing named input in their
context. Repeated calls/queries with the same identity collapse to their last
result and each named input's last occurrence. Distinct classes, objects, tables,
factors and enclosing contexts stay separate. Assignment targets and branch
positions do not create new call identities. Variables and properties refer to
worksheet state even when read inside a call. Void methods contribute an argument
context but no return value. A final property read also retains its recorded
`ObjectValue` as an opaque `receiver` alongside its property value; this receiver
representation does not participate in identity. Display categories, operators,
parentheses, declaration flags and branch results are not standalone identifiers.

Every value has `kind`, `type` (the recorded `ValueType`/`ResultType`, or null),
and `value`:

| Kind | Value representation |
|---|---|
| `number` | Canonical exact decimal **string**, never a JSON floating-point number. `1.00` → `"1"`; `0.09918` stays distinct from `0.0992`. |
| `string` | Original string, including empty strings and numeric-looking strings such as `001`. Untyped query parameters remain strings. |
| `boolean` | JSON true/false for Java Boolean or primitive boolean. |
| `null` | JSON null when the recorded value attribute is absent, replacing any prior value. The XML does not distinguish null from unavailable. |
| `opaque` | Original serialized representation and type, with `opaque: true`; no invented object fields. Includes entities, arrays, typekeys and other non-scalar types. |

Numeric handling recognizes Java Byte/Short/Integer/Long/Float/Double,
BigInteger/BigDecimal and primitive numeric type names. Decimal/scientific notation
is canonicalized without rounding; negative zero becomes `"0"`. Non-finite or
invalid typed numbers fail; expanded values above 100,000 digits fail explicitly.
This counts all rendered digits, including the leading zero of a fraction, but
excludes the sign and decimal point.
Types are retained, so identical numbers recorded with different types can differ.
A literal string `"null"` is not a missing value. Unknown recorded types are opaque,
not coerced into numbers or reconstructed into objects.

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
