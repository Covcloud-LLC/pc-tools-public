# Compare two retained worksheet captures

Compare these two local `pc-worksheet-final-values` version 2 JSON files and save
comparison JSON:

- Baseline: `<baseline path>`
- Candidate: `<candidate path>`
- Destination: `<output path>`
- Installed bundle: `<pc-worksheet-comparison directory>`

These are separately retained rating captures for the same job and quote branch.
The JSON does not prove that association or distinct rating executions. Use only
the two normalized files. Do not query PC, rerate, read raw XML, enrich
from job/product graphs, invent mappings, or repair/convert these inputs.

Read the bundle's README.md, then run its standalone Python 3.8+ script:

```sh
python3 <bundle>/scripts/worksheet-compare.py \
  --baseline <baseline> --candidate <candidate> --output <output>
```

Quote paths appropriately. Existing output is preserved unless I explicitly
request replacement using `--overwrite`; input aliases are always prohibited.
Do not use shell redirection to the destination.

Check both exit status and JSON outcome. Exit 0 is complete equal/different;
exit 3 publishes an incomplete comparison retaining only established pairs;
exit 2 publishes no new result. Report the saved path, outcome, changed counts,
and unresolved counts/reasons as applicable. Do not claim full equality from a
partial result or underlying object changes from opaque/receiver text. Detailed
human reporting is a separate workflow.
