# Inputs to gather per SBT line (for the rating access map / mutation-safety design)

Everything is read-only. "Checkout" = the PC configuration source at one revision.
Record the release and commit for every item; the tools pin them.

## A. What the existing pc-tools chain already needs (one line, one engine)

- [ ] A1. PC checkout at a known revision (`git rev-parse HEAD` or the build label).
      Needed paths: `modules/configuration/config/` (product model XML, `metadata/entity`,
      `extensions/entity`, typelists) and `modules/configuration/gsrc/` (all Gosu).
      Tool: pc-product-model-extraction `--source-root`, rating-workspace and rating-process extraction.
- [ ] A2. `admin/lib/pc-<version>.jar` from the same build (platform Gosu sources the checkout lacks:
      `CoverageDTO`, `EffDatedDTO`, `PolicyLineDTO`, `AbstractRatingEngineBase` if not in gsrc).
- [ ] A3. RTM ratebook XML export for every book the line rates with, including generic books
      (e.g. state tax). Export from Rate Management > Rate Books > Export, Active/promoted edition.
      Tool: pc-ratebook-extraction `--ratebook <export.xml>`.
- [ ] A4. Product and line codes exactly as PC spells them (`--product`, `--line`),
      and the rating engine class name(s) — all of them if a line has an entity and a DTO engine.
- [ ] A5. Rating worksheets for at least one rated job on that line: SQL Server read access to the
      PC database (job number with leading zeros) or an exported worksheets XML.
      Tool: pc-worksheet-extraction. This is the runtime evidence that a field was actually read.

## B. What the mutation-safety question adds (not captured by any tool today)

- [ ] B1. The LineModelObject sources: every class of the wrapper tree the engine builds
      (line, location/risk, coverable, coverage, cov term, modifier wrappers). Paths under gsrc.
      Why: backing kind per object and every setter (write-through to the entity or not).
- [ ] B2. How the tree is built: the factory/constructor code that walks the entity tree into the
      LineModelObject, and whether it copies values or holds entity references.
- [ ] B3. The engine's dispatch and base classes: `<Line>PolicyLineMethods.gs` (or SBT equivalent),
      the engine class, and the base classes it extends (`gw/rating/*.gs`, SBT abstract engines).
      Why: unresolved "engine call" facts (preRateStep, addCost, …) are where hidden writes live.
- [ ] B4. The rate routine plugin and cost-data wrappers: the `IRateRoutinePlugin` implementation
      (`gw.plugin.rateflow`), `CalcRoutineParamName.ttx`, and the parameter set definitions
      (in the RTM export, but confirm which typecodes map to which wrapper classes).
      Why: which in-scope parameters a routine may store to, and what a store reaches.
- [ ] B5. Parallel-rating configuration: `config.xml` values for `ParallelizedRatingEnabled`,
      `MaxRatingThreadPoolSize`, `ParallelRatingTimeoutPerCoverable`, any line-specific gate
      (CP demo uses `EnableCPDTOParallelRating`), and `shouldParallelizeRating()` overrides.
- [ ] B6. Any custom utilities the engine calls statically (the demo's `JurisdictionMappingUtil`,
      `Collections`): confirm they are under gsrc in the checkout (A1 covers them) or in a jar (A2).
- [ ] B7. One rated job's PolicyPeriod facts for the same worksheets as A5: slice dates, offering,
      base state. Why: the extractor marks `PolicyPeriod` reads unresolved; the worksheet values
      let us confirm what they were.

## C. Nice to have

- [ ] C1. A second rated job on the same line with a different structure (more locations/vehicles)
      — to see loop instances in the worksheets.
- [ ] C2. The implementation's own notes on thread safety or DTO plans, if any exist.
- [ ] C3. Which environment: version, whether it is SBT, and whether DTO-based rating is already
      used on any line there.
