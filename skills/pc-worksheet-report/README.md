# PC worksheet comparison report

Produce a local Markdown report from one saved comparison result: a short summary,
every recorded change with exact before/after evidence, and every unresolved
worksheet with its context and reasons. The audience is rating analysts and
PC developers. The skill and standalone prompt use the same validation,
evidence assembly and protected-save helper.

## Use

Supply a comparison JSON and a Markdown destination whose parent exists:

```sh
python3 /path/to/pc-worksheet-report/scripts/worksheet-report.py \
  --input /path/to/comparison.json --output /path/to/report.md
```

Python 3.8+ standard library only. Copy the entire bundle anywhere and run from
any directory; no comparator bundle, repository schemas, network or PC
checkout is required. Use [SKILL.md](SKILL.md) as an installed skill or supply
[PROMPT.md](PROMPT.md) with the bundle location and the two paths. Both Codex and
Claude Code discovery links point to the shared skill. The helper assembles the
report structure described by [REPORT_TEMPLATE.md](REPORT_TEMPLATE.md).

Existing output requires an explicit replacement request and `--overwrite`.
Input/output aliases (including symlinks and hard links) are rejected even with
that option. Reports are staged in the destination directory, flushed, and
published atomically. A symlink destination explicitly replaced with `--overwrite`
is replaced as a directory entry; its old target is not modified. Input is read
only. Invalid input or write failure leaves prior output intact. Do not edit a
comparison result to bypass validation. Keep private reports outside version control.

Exit **0** means a report was saved, including reports of incomplete comparisons.
The JSON status on stdout includes `comparison_outcome`, `complete`, findings,
unresolved count, output path and SHA-256 hashes. Exit **2** indicates validation,
path or I/O failure; the JSON error is on stderr and no new report is published.
Argument errors also exit 2 with argparse usage. No external publication occurs.

## Supported evidence

Accept `pc-worksheet-comparison` **version 1**, policy
`exact-reference-tag-interval-routine-v1`, declaring both normalized inputs as
`pc-worksheet-final-values` **version 2**. These version numbers are distinct.
Only the one result file is opened. Its hashes attest the bytes consumed and
record upstream provenance; reporting does not independently verify upstream files
or the caller's same-job/same-branch assumption.

Validation checks exact supported fields, value kinds/types, structured identity,
record uniqueness, worksheet index coverage, input counts/hashes, recorded pair
policy consistency, exact embedded change evidence, complete change inventories,
category counts, unresolved reasons/possible partners, outcomes and limitations.
It checks the result's declarations against its own embedded records, rejecting
contradictions rather than repairing evidence or creating pairs. It does not run
the comparator or open normalized captures. Unsupported versions/policies,
duplicate JSON keys, non-JSON constants, missing evidence and inconsistent detail
fail before publication. The five supported policy limitation statements must be
present; additional source limitations are retained verbatim as JSON text.

## Reading the report

The headline preserves equal/different/incomplete coverage. Pair headings retain
both original zero-based worksheet indexes; pair positions in `/pairs` are
separate locators. Exact worksheet context shows available description, qualified
FixedId, Tag presence/value, raw interval and routine on both sides. A missing
metadata key means absent; `""` means present-empty. Every identifier finding
includes full structured identity and both records, not a flattened dotted name.
A null record side means identifier absence. A present record with
`"kind": "null"` is an explicit null value, not removal. Context changes explicitly
record field presence. Receiver absence is a missing receiver key.

Evidence uses fenced JSON with escaped Unicode and control characters so source
Markdown/HTML cannot hide or execute content. Decode JSON escapes to recover exact
text. Decimal values stay exact strings; booleans stay JSON booleans. Evidence
blocks over 1,800 serialized characters go to a linked appendix in the same file,
without truncation. Every finding has a JSON pointer; the report records its source
path, byte hash, source link and both provenance hashes. Unchanged identifier
records remain available in the comparison JSON. Unresolved identifiers are not
reported as changes; their context, reasons, missing fields and possible partner
indexes are preserved.

Category counts overlap and must not be summed to count distinct identifiers.
Context-field counts use a different unit. Equal established pairs do not imply
full equality if any worksheet is unresolved. Complete equality is limited to
recorded evidence under the exact policy. Possible partners are not matches;
unresolved entries are not worksheet additions/removals, replacements, splits or
merges. Opaque values and receivers describe representations, not underlying
objects. There are no inferred premium totals, business impact, causation or
significance rankings, and no upstream enrichment or rerating.
