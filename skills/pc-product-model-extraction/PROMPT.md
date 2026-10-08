# Extract one native PC product and line

Use the scripts in the self-contained `pc-product-model-extraction` directory
(`<skill-dir>`); do not depend on repository-level tools. Python 3.8+ standard library
is enough. Flags, exit codes, scope and the capture format are in
[README.md](README.md).

1. Establish the inputs: a read-only PC10.2.3 checkout, the product and line
   codeIdentifiers, and an output directory outside the checkout. Ask only for a missing
   input. If product/line identity is unclear, run
   `python3 <skill-dir>/scripts/product-model-extract.py --source-root <checkout> --survey`
   and ask the user to pick. Never ask for entity roles, cost meanings or a decisions file.
2. Extract at full default scope:

   ```sh
   python3 <skill-dir>/scripts/product-model-extract.py \
     --source-root <checkout> --product <ProductCode> --line <LineCode> \
     --out <output-directory>
   ```

   Add `--coverage-tiers` or `--entity-closure seed` only when the user asks for narrower
   scope. Add `--overwrite` only when the user has authorized replacing
   the output, never just because an output-exists error occurred.
3. Revalidate the saved file, the `output` path the extractor printed:

   ```sh
   python3 <skill-dir>/scripts/validate-product-capture.py <saved-capture>
   ```

4. Report product/line, release, state, per-collection counts, saved path, validator
   result and any diagnostics. Never hand-edit the JSON or drop facts to clear an error;
   a correction comes from the source and a rerun.
