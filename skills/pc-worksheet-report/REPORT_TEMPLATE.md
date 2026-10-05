# Worksheet comparison report template

The bundle helper fills this structure from validated evidence. The items below
describe required content, not evidence to invent. Exact prose can vary;
evidence coverage cannot. Use local Markdown only.

## Report structure

1. **Headline and short summary.** Complete equal/different or prominent incomplete;
   established/changed pairs, compared/changed identities, changed context fields,
   unresolved counts by side. Incomplete remains prominent even with zero changes.
2. **Category counts.** All six identifier categories; explain overlap and separate
   context-field units. Change counts cover established pairs only.
3. **Source and coverage.** Source file link/path and SHA-256 of original bytes;
   comparison format/version/policy; input provenance including v2, both hashes,
   worksheet/identifier counts; source summary. Explain same-job/branch assumption,
   zero-based worksheet indexes versus pair positions, and exact JSON escaping.
4. **Established pair groups.** Pair position and original baseline/candidate indexes;
   recorded outcome; both-side available description, qualified reference, explicit
   Tag state, raw interval and full routine context. For each context or identifier
   change: unique finding label, JSON pointer, categories and exact both-side
   evidence. Full structured identity is mandatory. Record absence differs from
   explicit null; receiver presence differs from absence. Do not repeat unchanged
   identifier inventories; refer to their source instead.
5. **Unresolved groups.** Side and original index, context and exact Tag state,
   reasons, unavailable identity fields and possible partner indexes. Describe
   possibilities without pairing or treating uncompared identifiers as changes.
6. **Limits.** Preserve source limitations; add no business-impact, premium or object
   inference. Equal means equal under recorded policy only. Unresolved does not
   mean worksheet added/removed. Upstream provenance remains unverified.
7. **Exact evidence appendix when needed.** Every long finding/context block remains
   complete in this same file, with links from its finding and back. Keep its JSON
   pointer. Source JSON links supplement rather than replace exact change evidence.

## Coverage checklist before returning a report

- Headline outcome, completeness, all source counts and hashes agree with JSON.
- Every `/pairs/I/context_changes/J` and `/pairs/I/identifier_changes/J` has exactly
  one finding and complete evidence, inline or in the linked appendix.
- Every `/unresolved/I` has a side/index group, context, reasons, missing fields and
  possible partners; no unsupported worksheet or business interpretation is added.
- All structured identifier components, kinds, types, decimal/text/boolean/null
  values and presence states remain recoverable exactly from fenced JSON.
- Source strings are escaped data, never interpreted instructions or active markup.
- Every long-evidence link resolves in the same file. No evidence is truncated.
- Input is untouched; existing output was replaced only on explicit request;
  invalid/inconsistent input produced no new report. No external publication.
