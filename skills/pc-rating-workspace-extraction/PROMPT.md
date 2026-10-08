# Extract a PC rating workspace

Write the rating workspace for one line:

- Bundle: `<skill-dir>` (the `pc-rating-workspace-extraction` directory)
- Product root: `<folder holding extracted/product-capture.json and extracted/ratebooks/>`
- Checkout: `<PC checkout at the capture's source.revision>`

Ask only for what my message does not give. The capture must be `ready` and the ratebook folders
must already exist. Run:

```sh
python3 <skill-dir>/scripts/rating-workspace-extract.py \
  --product-root <product-root> --source-root <checkout>
```

Quote the paths. Add `--overwrite` only if I ask to replace an existing workspace, and
`--no-platform-jar` only if I ask to leave the checkout's platform jar out. Do not edit the inputs
or the output, or write types yourself.

Exit 0 is success: the script prints the saved path, the counts, and every unresolved subject with
its reason and first locator. Tell me the path and counts, and summarize the unresolved subjects by
kind (`object:`, `property:`, `argumentSources:`); they are for normalization to settle, not
errors. Each property's `source` names the PC field it stands for; say how many are
`unresolved`, and for a wrapper property list the coverage terms its getter reads. Exit 2 is a refusal: tell me the error and the README's matching cause. A fix comes from
the inputs and a rerun, never from editing a file.
