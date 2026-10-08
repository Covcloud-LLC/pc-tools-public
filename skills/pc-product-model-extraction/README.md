# Native PC product extraction

Extract one classic PC 10.2.3 product and selected line into
`product-capture.json`, using Python 3.8+ standard library. The capture format is
`pc-tools.product-capture/1.0.0`. The extractor, schema and validator are self-contained;
copy this entire directory to install them. The workflow is in [PROMPT.md](PROMPT.md).

## Run

```sh
python3 <skill-dir>/scripts/product-model-extract.py \
  --source-root /path/to/pc-checkout \
  --product CommercialProperty --line CPLine --out /path/to/capture
python3 <skill-dir>/scripts/validate-product-capture.py \
  /path/to/capture/product-capture.json
```

| Flag | Meaning |
| --- | --- |
| `--source-root` | Required input checkout; absolute or relative path. Its configuration module is `modules/configuration`, beside `project-version.properties`. |
| `--product`, `--line` | Exact, case-sensitive product and PolicyLinePattern identifiers. |
| `--out` | Required destination directory, outside the source. The capture is written directly in it. |
| `--overwrite` | Replace this tool's existing output. |
| `--coverage-tiers` | Default `mandatory,electable,scripted`; a subset narrows scope. |
| `--entity-closure` | Default `composition` follows arrays/one-to-ones, supertypes and delegates. `seed` narrows composition traversal. |
| `--survey` | Print product identities and their line joins; writes no files. |

A source export need not be a Git repository. `revision` and `dirty` are recorded only when
the source root is the top level of its own Git checkout; otherwise both are null, so the
capture has no source identity. This includes an export copied inside some other checkout. Release metadata in `project-version.properties` must identify PC10.2.3,
otherwise the capture is blocked.

| Exit | State / effect |
| --- | --- |
| 0 | `ready`: mechanically complete, full supported scope. Writes `product-capture.json`. |
| 3 | `partialScope` (intentional narrowing) or `blocked` (unresolved/unsupported facts). Writes `product-capture.diagnostic.json`. |
| 2 | Fatal invocation, malformed/unreadable input, invalid artifact or publication failure. Publishes nothing. |

## Scope

The capture holds native declarations only. It assigns no entity roles, portable
ownership, attachment, exposure or charge meaning; that interpretation belongs to
normalization, and only a revalidated full-scope `ready` capture is input to it. The
tool writes no YAML and takes no decisions file.

Excluded: availability lookups, script bodies, question/template contents and rating
execution. APD-generated, visualized-only and other releases get unsupported
diagnostics and cannot become ready.

## Native format

The [schema](schema/product-capture.schema.json) and
[validator](scripts/native_capture_validation.py) define the one capture format.
Each native declaration is an ordered node with `kind`, `attributes`, `children`
and `sourceRefs`. `attributes` holds **native XML names and string values**:
`public-id`, `codeIdentifier` and `optionCode` remain distinct; `0.2500`, `false`
and empty strings stay unchanged. Omitted attributes stay absent. `text` retains
native element values. XML entity decoding and XML attribute whitespace rules apply;
this is a declaration capture, not a byte-for-byte XML archive. No Boolean/default
conversion is implicit. The file is UTF-8 JSON with one-space indentation.

| Root | Contents |
| --- | --- |
| `source` | Observed release/build/platform/language from `project-version.properties`, configuration root, generation mode, the checkout's Git commit SHA as `revision`, and `dirty`. `dirty` is true when `git status` shows changes or untracked files under `modules/configuration/config` or `project-version.properties`, the only paths the extractor reads; changes elsewhere in the checkout do not count. Nothing else about the source or the tool is recorded: `revision` and `dirty` identify the input. |
| `scope` | Selected product/line, available product line joins, clause kinds, requested tier/closure scope and explicit exclusions. |
| `product` | Native product declaration, all actual offerings (an empty list when the product declares none, as CommercialProperty does) and selected-product display properties. |
| `policyLinePattern` | Native line declaration, selected clauses, explicit line properties, display properties and jurisdictional modifier bounds. |
| `entities` | Each native entity/delegate name with ordered `declarations`: metadata then extension files, each retaining its own attributes and members. |
| `typeLists` | Each native list with ordered base, internal and extension declarations. Entity-derived typekeys retain `entityTypes` headers, without expanding other-line entities. |
| `costCodes` | Exact system-table rows, including empty elements and the native `ChargePatern` spelling. |
| `costModel` | Native line-array and transaction-array pointers, cost inheritance members and matching PolicyPeriod array declarations; null for inspected absence of cost arrays. |
| `summary` | Captured counts and inspection state for named collections. |
| `diagnostics` | Concrete mechanical errors/warnings with native source locators when available. |

Children preserve source order. Declaration layers are retained separately, ordered
by metadata/extension directory then filename. This ordering is an inspection order,
not a claim about runtime override precedence. Base fields, delegate fields and
`*-override` members remain on their declaring records. Ambiguous/missing field or
reference targets block readiness; no first-match resolution or effective field
flattening occurs. Follow native `supertype` and `implementsEntity` edges to find
inherited fields such as Coverage.PatternCode.

Foreign-key attributes retain every raw target, including on cost entities.
`referenceStatus` distinguishes captured, external and intentionally narrowed targets.
Building and PolicyLocation can stay external. Native `owner`, `cascadeDelete`,
`nullok` and other flags remain exact source attributes.

Typelist `.tti`, `.tix` and `.ttx` declarations remain separate. For example, a base
empty CPBlanketType filter and its extension includes are both retained; no effective
runtime domain is inferred. References differing from the resolved identifier retain
raw spelling plus local `resolution` evidence. A typelist name that matches a declared
typelist only when ASCII case is ignored (PersonalAutoLine's `NumberofAccidents` for
`NumberOfAccidents`) resolves only when `source.release` reports `platform-version`
`10.203.1` and `gosu-version` `1.17.4`. The resolution has `status:
verifiedCaseInsensitive`, the `target` name and sourceRefs to the target's declarations.
Any other platform or language version, a non-ASCII spelling or more than one candidate
blocks that reference. Public-ID/code collisions are ambiguous, never first-match.

The schema contains the supported native declaration vocabulary. Unknown in-scope
members/attributes are retained with blocked diagnostics. Scripts are omitted and their
element names listed in `excludedChildren`. Association attributes remain native facts.
Display-property values retain source spelling, including escape sequences; no locale
fallback is performed.

## Validation and output protection

The validator reads only the saved JSON and its local schema. It rejects duplicate
JSON keys and invalid shapes; checks that every `sourceRefs` path is source-relative
(no leading `/`, no `\` and no `..`); verifies identity, product/offering joins, references,
inheritance/delegates, filters, cost pointers, counts and state. It recomputes readiness
instead of trusting the stored label.

A standalone validator cannot detect a declaration omitted from both records and
summary. Source-grounded tests compare retained facts to native inputs. The source
`revision` plus `dirty: false` identifies the inputs.

Publication stages, flushes, rereads and validates a complete file before atomic
replacement. Existing targets require `--overwrite`. Diagnostic runs never replace
the ready filename. Successful ready overwrite removes an obsolete owned diagnostic;
other files are preserved. These two filenames are reserved to this tool. Symlink
output files/directories and output within the source are refused. The source is
read-only: the extractor never writes inside it.
