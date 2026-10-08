# pc-rating-access-report

Writes one PC line's **rating access report**: for each rating engine of the line,
every object the engine and its calc routines touch with what backs it (an entity, a wrapper over
an entity, a DTO copy, cost data, a plain class, a scalar), every write with its actor and target,
every entity-backed read including lookup arguments, the parameter bindings each engine passes to
its routines, the facts the extractions could not resolve, and headed manual sections for what no
tool captures. The report is the input to a design question: can calc-routine inputs be mutated
inside a rating engine without writing back to the underlying entities?

`scripts/rating-access-report.py` reads only the product root's `extracted/` files, which the
four extraction skills write (`pc-product-model-extraction`, `pc-ratebook-extraction`,
`pc-rating-workspace-extraction`, `pc-rating-process-extraction`). Python 3.8+, standard library
only, no network, no checkout access. The workflow that runs the extractions, the script and the
manual sections is in [PROMPT.md](PROMPT.md).

## Inputs

All read-only, all under `<product-root>/extracted/`.

| Input | What is read |
| --- | --- |
| `rating-workspace.json` | `pc-rating-workspace-extraction` output: the line, the checkout revision, `objects[]` (key, kind, class, entity, subtype, properties with their `source`) and `scalars[]`. |
| `rating-process-<Engine>.json`, one per engine | `pc-rating-process-extraction` output: `engine`, `dispatch`, `assumedGates`, `pins`, `revision`, and `units[]` with their dataflow facts (`writes`, `entityWrites`, `entityReads`, `beanInserts`, `engineMembers`, `unresolved`) or, for calc-routine units, `ratebook` and `binds[]`. |
| `ratebooks/<book>-<edition>/routines/*.json` | `pc-ratebook-extraction` output: each routine's `parameterSet.parameters[]` and `steps[]` with their store target, operands and arguments. |
| `ratebooks/<book>-<edition>/tables/<code>.json` | Each table's `argumentSourceSets[]`, read for the steps that consult the table. |

Nothing else is read: not the checkout, not the product capture, not `normalized/`.

## Run

```sh
python3 skills/pc-rating-access-report/scripts/rating-access-report.py \
  --product-root <product-root>
```

| Flag | Meaning |
| --- | --- |
| `--product-root` | Product root holding `extracted/rating-workspace.json`, at least one `extracted/rating-process-<Engine>.json` and `extracted/ratebooks/`. Required. |
| `--out` | Directory for both outputs. Default: the report at the product root, the JSON under `extracted/`. With `--out`, both land in that directory. |
| `--overwrite` | Replace existing outputs. Without it an existing report or JSON is refused. |
| `--help` | Usage; exit 0. |

Exit 0 writes both files and prints a summary: the saved paths, then the counts per section
(engines, objects and scalars; store targets by object, locals, no target and flagged; per engine
the cost data writes, entity writes, bean inserts and member accesses; the entity-backed read
rows per list; the bindings per engine by kind and the routines in no exported book; the
unresolved units and facts per engine), every read the inputs could not establish, and
the number of manual sections to fill.

Exit 2 prints `{"status": "refused", "error": "..."}` on stderr and writes nothing, for: a product
root that is not a directory, no `extracted/rating-workspace.json`, no
`extracted/rating-process-<Engine>.json`, a workspace that names no line, a rating-process
document whose `product`, `line`, `revision` or `dirty` differs from the workspace's, a
`ratebook.routine` path that leaves `<product-root>/extracted/`, a table code that leaves its
book's `tables/` folder, an input that is not JSON, or an existing output without `--overwrite`.
The message names the cause.

Two runs over the same inputs write byte-identical files: rows keep their source order or are
sorted, and nothing carries a timestamp.

## Outputs

`<product-root>/RATING-ACCESS-REPORT-<Line>.md` and `<product-root>/extracted/rating-access.json`
(format `pc-tools.rating-access/1.0.0`). The JSON holds the same rows as the report's tables,
plus the sha256 of the workspace and of each rating-process document, and the paths of the
routine files it read. Its top-level keys:

```text
formatVersion        pc-tools.rating-access/1.0.0
product, line        from the workspace
revision             checkout commit the workspace records; configurationRoot beside it
inputs               workspace {path, sha256}; ratingProcesses [{path, sha256, engine}];
                     ratebooks [folder]; routines [path]
engines[]            engine, base, plugin, at, overrides, dispatch, assumedGates, sourceRoot,
                     configurationRoot, revision, dirty, units {total, gosuMethod, calcRoutine}
objects[]            key, kind, className, entity, subtypeEntity, parent, properties (count),
                     sourceKinds {kind: count}, parameters
scalars[]            parameter, type, book, parameterSet, routines
storeTargets         rows [{book, routine, step, target, object, property, kind, flagged, storeType}];
                     counts {rows, byObject, locals, noTarget, flagged}
engineWrites[]       per engine: units [{unit, at, costData [field], entityWrites [{target, shape,
                     property | method + declaredIn, at, backing, through}], beanInserts [{entity,
                     at, backing, through}], memberAccess [{name, access, declaredIn, declaredAt,
                     at}]}]; counts
entityReads          units [{engine, unit, entity, shape, property | method + declaredIn, at,
                     backing, through}]; inScopeOperands [{book, routine, step, stepType, where,
                     object, property, wholeObject, kind, overridesSource?}]; lookupArguments
                     [{book, routine, step, stepType, table, tableBook, key, object, path, kind,
                     via}]; gaps [text]; counts
bindings[]           per engine: rows [{unit, routine, parameter, expression, type, kind, from,
                     verify, paramType, useWrapper, wrapperClass, writable, at}];
                     unboundRoutines [unit]; counts {rows, byKind, unboundRoutines}
unresolved[]         per engine: units [{unit, at, facts [{fact, expression, reason, at}]}]; counts
manualSections[]     heading, question, columns
baseClassMembers[]   class, members [{member, declaredAt, access, calledFrom}]
counts               the counts of every section, as the summary prints them
```

The report's `## ` sections, in order:

1. **Environment and engines.** One row per rating-process document: engine, base class, plugin,
   declaration locator, the source root as the extraction recorded it, the revision, the unit
   counts; then each engine's dispatch branches, assumed gates and overrides.
2. **Object backing.** The workspace objects: key, kind (`entity`, `wrapper`, `dto`, `gosuClass`,
   `costData`), class, entity, subtype entity, parent, property count, property source kinds; the
   scalar parameters on one line.
3. **Routine store targets.** Every `assignment` step of every routine in the exported books. The
   target is the step's in-scope parameter and value (`costdata.BaseRate`); when the in-scope
   parameter is empty, the store location names a local; when both are empty the step has no
   target. A target whose object is of kind `entity` or `wrapper` is **flagged**.
4. **Engine writes.** Per engine, per method unit: cost data fields written (`cost data
   (engine)`), entity writes in both shapes (`{target, property}` and `{target, method,
   declaredIn}`), bean inserts, and engine member accesses other than reads. The backing of an
   entity write or bean insert is the kind of the workspace object whose `entity` is the target,
   then the wrapper whose subtype entity is the target, else `unknown`; `through` names that
   object.
5. **Entity-backed reads.** Three lists under one rule (the object is backed by an entity or a
   wrapper over one): the units' `entityReads` in both shapes; routine in-scope operands at top
   level and inside operand arguments, with an empty value reported as a whole-object pass; and
   lookup arguments: for each step operand that consults a table, the bound sources of the
   argument source set with the code it names and the routine's parameter set (`parameterSet`
   equal to the routine's `parameterSet.publicId`), each replaced by a step argument with
   `overridesSource` on the same key, kept when the resulting root is an entity or wrapper
   object. When no set with that code is bound to the routine's parameter set, only the
   overriding arguments are known. A table the folders do not hold, a lookup with no matching
   set and keys the step does not override, or an in-scope parameter or lookup root that is
   neither a workspace object nor a scalar is listed under "Not established", so an unresolved
   read never reads as no read.
6. **Routine parameter bindings per engine.** One row per routine parameter of each calc-routine
   unit: the bound expression, type, kind and origin from the unit's `binds[]`, beside the
   parameter set's `paramType`, `useWrapper`, `wrapperClass` and `writable` from the routine file
   the unit's `ratebook.routine` names. A unit without `ratebook` is marked "routine not in an
   exported book: store targets not established" with "not established" in those cells.
7. **Unresolved facts.** Per engine, per unit: every `unresolved` row verbatim (`fact`,
   `expression`, `reason`, `at`). A write hidden in one of these would not appear above.
8. **Manual sections**, each a headed placeholder with the question to answer and the columns of
   the answer: wrapper setters and reference or copy; tree builder; base-class method bodies (the
   member calls to answer for are listed by declaring class; member writes stay in the engine
   writes section); rate-routine plugin wrappers;
   configuration values; runtime evidence.

Names, types and locators are PC's own as the extractions recorded them; the script
derives no fact of its own beyond the joins above.
