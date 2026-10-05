# Worksheet comparison

Compare two retained PC rating captures from the same job and quote
branch. Inputs are `pc-worksheet-final-values` **version 2** JSON; output is
self-contained comparison JSON for downstream reporting. Python 3.8+ standard
library is the only requirement. The entire bundle works from any directory.

```sh
python3 /path/to/pc-worksheet-comparison/scripts/worksheet-compare.py \
  --baseline /path/to/baseline.json --candidate /path/to/candidate.json \
  --output /path/to/comparison.json
```

The output parent directory must exist. Add `--overwrite` only to explicitly
replace an existing destination. Inputs are preserved, including when an output
aliases either input through a symlink, parent-directory alias or hard link.
Comparing the same input twice is allowed. A symlink output to an unrelated file
is refused by default; explicit overwrite replaces the symlink itself.

[SKILL.md](SKILL.md) supplies the assistant workflow;
[PROMPT.md](PROMPT.md) works without skill discovery. No XML, database, product,
job graph, network access, dependency install or manual map is used at runtime.

## Status and publication

| Exit | Outcome | Publication |
|---|---|---|
| 0 | `equal` or `different` | Complete comparison JSON. Changes are findings, not process failures. |
| 3 | `incomplete` | Comparison JSON with established pairs and all unresolved entries/reasons. |
| 2 | Error | No new result; any prior output is preserved. Invalid CLI usage also returns 2. |

The CLI prints a small JSON status with output path and summary on stdout. Runtime
errors print JSON with `status`, `category`, and `message` on stderr. Categories
include `invalid_input`, `unsupported_version`, `input_read`, `path_error`,
`input_output_same`, `output_exists`, and `output_write`.

Both inputs are fully validated before publication. The script writes and fsyncs
a temporary file beside the destination, then atomically links it for no-clobber
publication or replaces the destination for explicit overwrite. The default mode
requires filesystem hard-link support. A concurrent destination creation is
preserved in default mode. A rare temporary-file cleanup failure adds a warning
to CLI status after successful publication; the published JSON remains complete.
Results contain no timestamps or absolute paths, and identical input bytes/policy
produce identical result bytes regardless of destination. Input changes during
reading and adversarial filesystem mutation are outside this local-file workflow;
use retained, stable captures.

## Valid inputs

Root fields are exactly `format`, integer `version: 2`, and a nonempty `worksheets`
array. V1, mixed versions, unknown versions and POC envelopes fail; there is no
legacy mode. Unknown fields, duplicate JSON keys, duplicate structured identifiers,
non-JSON numbers, invalid UTF-8/surrogates and invalid shape/value combinations
fail without dropping data. Worksheet order and identifier order do not count as
changes. Input hashes/indexes remain source locators and may differ with order.

Each worksheet has exactly `metadata`, `routine`, and `identifiers`:

- Metadata permits optional text `FixedId`, `Tag`, `EffectiveDate`,
  `ExpirationDate`, `Description`.
- Routine permits optional text `RateBookCode`, `RateBookEdition`, `RoutineCode`,
  `RoutineVersion`.
- Identifiers may be empty. Every record contains a full `identifier`, a `value`,
  and optionally a property `receiver`. Repeated worksheet contexts remain
  separate entries; they are valid but may be ambiguous.

All metadata may be missing or empty without making the document invalid.
The producer preserves arbitrary reference/date text; validity is distinct from
sufficient identity for pairing.

Identifiers follow the producer grammar: variables have a nonempty name;
properties add object name/type; functions have class/name/context; queries have
table/factor/source/context; arguments and parameters have name/owner context.
Context arrays contain exact structured calls and named inputs. Enclosing nested
functions/queries use function-or-method/argument pairs; argument owners are
functions or root methods, and parameter owners are queries. Variables/properties
have worksheet scope. All required names/types are nonempty; query source may be
empty. Dotted strings never replace structured identity.

Values contain `kind`, `type` (exact string or null), and `value`:

| Kind | Representation |
|---|---|
| `number` | Canonical exact decimal string for primitive/Java numeric types, including BigInteger/BigDecimal. No exponent, plus sign, leading zeros, negative zero or trailing fractional zeros; at most 100,000 digits excluding sign/point. |
| `boolean` | JSON boolean with primitive/Java Boolean type. |
| `string` | Exact text with absent type (`null`) or String/Character/char type. |
| `null` | JSON null, with any recorded type retained. Distinct from an absent identifier. |
| `opaque` | Exact text with a non-scalar type (including empty type text) and `opaque: true`. |

A receiver is `{type, value, opaque: true}` on a property only, where `type`
equals the property's object type and `value` is exact text. Receiver presence
is distinct from absent receiver. No float conversion, numeric tolerance,
object-ID stripping, type coercion, last-value reduction or object reconstruction
is performed.

## Correspondence and uncertainty

Only mutual unique exact matches pair: qualified `FixedId`, Tag presence/value,
raw effective and expiration text, and nonempty `RoutineCode`. A usable qualified
reference contains one colon with nonblank parts; the entire original string is
compared. Dates and RoutineCode must be nonempty text, with no trimming, date
parsing, timezone normalization or case folding. Absent Tag and present-empty Tag
are both known states and differ.

Missing/empty/unusable required fields are unknown discriminators. Two entries
could compete when every discriminator known on both sides agrees. Establish a
pair only when both identities are complete and each has exactly one possible
partner. A partial or duplicate identity with compatible known fields withholds
the affected pair; known conflicting fields preserve independent pairs. A full
identity gap may affect every compatible partner. Possible-partner indexes explain
uncertainty and never assert correspondence.

Changed references, Tags, raw intervals or routine codes remain unresolved. There
is no inferred worksheet addition/removal, split, merge or replacement. Within
established pairs, full structured identifier identity determines additions,
removals and changes. Descriptions and book/routine versions are compared after
pairing.

## Result contract

Output root: `format: pc-worksheet-comparison`, `version: 1`, fixed `policy`,
`outcome`, `complete`, `inputs`, `summary`, `pairs`, `unresolved`, and `limits`.
The output version is independent of input version 2.

`inputs.baseline/candidate` include original-byte SHA-256, format/version,
worksheet and identifier counts. Every pair contains both-side `index` (zero
based), all metadata, all routine fields and all final identifier records,
including unchanged records. Unresolved entries preserve the same evidence,
the side, reasons, unavailable identity fields and possible opposite-side indexes.
No input reopening is needed to explain changes or unresolved entries.

`context_changes` name the `metadata`/`routine` field and exact before/after
presence wrappers: `{present: false}` or `{present: true, value: ...}`.
`identifier_changes` carry the full structured identity, categories and complete
before/after records. JSON null at this record level means identifier absence;
a present null value remains inside its record's value object.

Categories and summary semantics:

- `value`: recorded kind or exact content changes (including opaque text).
- `type`: kind or recorded type changes. A type-only change does not imply value
  content changed; a kind change is counted in both value and type.
- `opaque_text`: opaque kind/text changes on either side; type-only changes use
  `type`. These counts overlap with value changes.
- `receiver`: receiver presence or representation changes.
- `added` / `removed`: identifier presence changes within established worksheets.
  These use presence categories alone, rather than comparing an absent value.
- `metadata_routine`: category of context fields, counted separately as
  `changed_context_fields`, not attributed to every identifier in the worksheet.

`changed_identifiers` counts each changed identity once per paired worksheet,
including additions/removals. Each applicable identifier category counts it once;
category totals overlap. `compared_identifiers` is the union of identifiers within
established pairs. `changed_pairs` counts pairs with identifier or context changes.
`unresolved_baseline/candidate` count entries, not inferred business groups.
All identifier/context change counts cover only established pairs.

Any unresolved entry takes precedence: outcome `incomplete`, `complete: false`,
exit 3, even if established pairs are all equal. Otherwise any counted change
makes `different`; zero changes makes `equal`. Complete coverage is limited to
this policy. Same-job/branch and distinct-rating provenance are caller assumptions;
opaque equality is recorded representation only. The result repeats these limits.
