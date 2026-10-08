# pc-rating-process-extraction

Turns how one PC line of business is rated today by one engine into one source-native
**PC Rating Process** document (`pas-adapters.policycenter-rating-process/4.0.0`):
dispatch, the call graph from the engine roots, units of work with their method bodies and ratebook
routines, what each method unit reads and writes outside the ratebook routine (its dataflow facts),
lookups and emissions, every fact with a locator. A witness, not an engine: no formula, rate,
factor value, table row, or rounding rule enters the document outside the verbatim method bodies and
the ratebook routine text it points at.

Its product input is the line's product capture, `<product-root>/extracted/product-capture.json`,
written by `pc-product-model-extraction`: a `ready` `pc-tools.product-capture/1.0.0` document for
the same product and line, taken at the checkout's commit. From it the extractor builds the
lookups it interprets PC code with: clause pattern codes and their cov terms, entities with their
members, supertypes and implemented entities, cost entities (`costModel.costEntities` and every
captured entity whose supertype chain reaches one), the line's entity and the capture's revision.
The document pins the capture and joins it by verbatim native identifier.

The dataflow facts serve analyzers that judge which rating work can run in parallel. PC's
parallel rating framework (`gsrc/gw/rating/AbstractParallelRatingEngineBase.gs`) requires parallel
code to insert no beans, use preloaded FX rates, use its thread's own PolicyLine and reach costs
only through the synchronized `addCost`/`addCosts`; the facts are what those conditions are judged
from. A fact the extractor cannot establish is an `unresolved` row, never left out.

`scripts/rating-process-extract.py` is Python 3.8+, standard library only, with no network. It
reads the product capture, the engine class and every class in its directory tree, the engine's
base classes and the other gsrc classes, enhancements and DTO classes its code names (for member
declarations and types; never as units), the `createRatingEngine` branches, `config.xml` (when a
gate names it), `rating_adj_factors.xml` (row shape only), `modules/base.zip` and the ratebook
folders. The workflow, with its human gate, is in [PROMPT.md](PROMPT.md).

## Running it

The skill directory is self-contained; copy it to install it. The checkout machine needs Python
3.8+ and nothing else.

```sh
python3 skills/pc-rating-process-extraction/scripts/rating-process-extract.py \
  --source-root ../pc-checkout \
  --product CommercialProperty --line CPLine \
  --product-root work/pc/CPLine \
  --decisions work/pc/CPLine/decisions/rating-process-CPRatingEngine.yaml --force
```

The decisions file names the engine (`selectedEngine`), so `--engine` is not needed on a re-run. A line with more
than one engine (CPLine's `CPRatingEngine` and, when `PCConfigParameters.EnableCPDTOParallelRating`
is true, `CPDTORatingEngine`) gets one run, one document and one decisions file per engine.

## Flags

| Flag | Meaning |
| --- | --- |
| `--source-root` | Checkout root; every checkout locator path is relative to it. Ratebook locators are relative to the product root. Recorded in `pins.sourceRoot` as passed, so pass it relative. Default `.`. Its configuration module is `modules/configuration`. |
| `--product`, `--line` | Product and PolicyLinePattern `codeIdentifier`. Required; must match the capture's `scope`. |
| `--engine` | The selected engine class, one of the `createRatingEngine` branches. Also settable as `selectedEngine` in the decisions file; the flag wins. A fully qualified name matches on its last segment. It names the output file. |
| `--revision`, `--dirty` | For a non-git export. A git checkout needs neither. |
| `--decisions` | The consultant decisions file, YAML (see The decisions file). |
| `--product-root` | Product root. Required. Holds `extracted/product-capture.json` and `extracted/ratebooks/` (the folders `pc-ratebook-extraction` writes: `<folder>/book.json`, `routines/<code>.json`, `routines/<code>.txt`, `tables/<code>.json`); the document goes to `<product-root>/extracted/rating-process-<EngineClass>.json`, the engine from `--engine` or the decisions file. The extractor writes only that file. |
| `--force` | Rewrite an existing `extracted/rating-process-<EngineClass>.json`. |
| `--survey` | Print the dispatch candidates and exit. Needs no `--engine`; writes nothing. |

Exit code 2 with a message on: a missing product capture, a capture whose `state` is not `ready`
or whose `formatVersion` is not `pc-tools.product-capture/1.0.0`, a capture for another product or
line, a capture whose `source.revision` differs from the checkout's commit, a non-git checkout
without `--revision`, no engine to name the output file, a decisions file outside its YAML subset
or its keys (the message names the file and line), an existing document without `--force`, or an
engine whose traversal finds no unit.
It never guesses past one of those, and writes nothing.

## Output

One run writes `<product-root>/extracted/rating-process-<EngineClass>.json`, the document
described below, and prints a summary for the human gate:

- the product, line, engine and document path; the revision (with `(dirty)`) and the baseline read;
- `counts:` capture clause patterns, entities, cost entities and typelists; dispatch branches
  drafted; overridden members; units by kind; emissions and their unresolved key dimensions;
  binds; ratebook binds, missing and ambiguous routines, parameter mismatches and reads; lookups;
  unresolved loop entities; rows per dataflow fact list; flow edges, guards and loops;
  cross-document references unresolved; ratebook files digested;
- `Ratebook joins:` the routines bound, missing, and ambiguous (with every candidate folder);
- `Open questions:` every `<VERIFY>` the run could not settle and every `DRAFT` it parsed for the
  consultant to confirm;
- `Readiness:` a checklist ticked only for what the run established.

## The document

`rating-process-<EngineClass>.json` is JSON, indented two spaces, with a method body as one string
whose line breaks are `\n`. Keys in this order:

| Key | Holds |
| --- | --- |
| `$schema`, `schemaVersion` | `urn:pas-adapters:schema:policycenter-rating-process:4.0.0` and `pas-adapters.policycenter-rating-process/4.0.0`. |
| `product`, `line`, `revision`, `dirty` | The natural key. `revision` is the checkout's commit (or the `--revision` given for a non-git export); `dirty` is true when `git status --porcelain` reports a change under the paths the run reads: the configuration root `modules/configuration` and, when it opens one, the `modules/base.zip` baseline. A change elsewhere in the checkout leaves it false. |
| `productModel` | The product capture pin: `capture` (`extracted/product-capture.json`, relative to the product root), `revision` and `dirty` (the capture's `source.revision` and `source.dirty`), `product`, `policyLinePattern`. |
| `engine` | `selected`, `base` (its base class), `plugin` (the registered `IRatingPlugin`), `at`, and `overrides`: every overridden member with `at` and a `status` when it is not vanilla. |
| `dispatch` | Every `createRatingEngine` branch in source order: `engine`, `rateMethod` when the branch tests one, `when` (the enclosing conditions, verbatim), `selected: true` on the branch the document describes, `at`. |
| `assumedGates` | From the decisions file, verbatim: `name`, `value`, `at` (a list). |
| `flow` | `rateSlice` and, when the engine defines it, `rateWindow`: each `at` plus `calls`, the edges from that root. |
| `units` | One entry per unit of work, each once. |
| `emissions` | One entry per (constructing unit, CostData class, selecting switch case). |
| `lookups` | One entry per `RateAdjFactorSearchCriteria` factor name: `key`, `kind: rateAdjFactor`, `factorName`, `dimensions`, `resultColumns`, `at`. No rows. |
| `pins` | `sourceRoot` as passed, `configurationRoot`, `generationMode` from the product capture and, when ratebook folders were searched, `ratebookFiles` (see **Ratebook digests** under What is mechanical). |

**Locators.** Every fact carries `at`, one string `<path>[:<line>][#<symbol>]`: a checkout path
relative to `--source-root`, or a ratebook path relative to the product root (`...routines/<code>.json#step 9`).
Only `assumedGates[].at` is a list, because a gate's evidence sits in more than one file.

**Edges** (`flow.<root>.calls[]` and `units[].calls[]`). One entry per call site, in evaluation
order: `call` (a method key) or `routine` (a calc routine code), `args` (the argument expressions
verbatim, whitespace collapsed), `for` (the loops in effect, outermost first: `var`, `in`, and
`entity` when the collection holds a captured entity, `class` when it holds a class under
`gsrc` such as a rating DTO (`CPLocationDTO`), else `verify`; `parallel: true` for
`rateInParallel`), `when` (the guards in effect, outermost first, as the Flow rules below spell
them), `emits` (the cost entity this call site constructs, when the callee selects its CostData
class from a string literal the site passes), `calls` (only on an edge to a method that is not a
unit: that helper's own edges, folded inline), and `at`. An edge to a unit stops there: the unit's
own edges live once, under `units[].calls`, as the union over every site that reaches it.

**Units.** Keyed `<SimpleClassName>.<method>` (overloads with their parameter types) for a
`gosuMethod`, or the routine code for a `calcRoutine`. Fields, in order: `key`, `kind`, `at`,
`status` (only when not vanilla; every calc routine is `noBaseline`), `appliesTo` (clause patterns;
`[]` when line-wide), `calls` (gosuMethod), `writes` (CostData members assigned), `scratch` (other
values written, only when there are any), `reads`, `emits` (gosuMethod: cost entity names), the
seven dataflow fact lists (gosuMethod only, each always present, possibly empty: `entityReads`,
`engineMembers`, `entityWrites`, `beanInserts`, `crossInstance`, `cacheAccess`, `unresolved`),
`source` (gosuMethod: the method body verbatim, common indent removed, as one string),
`ratebook` (calcRoutine, when bound: `book`, `edition`, `routine`, `text`, `sha256`,
`parameterSet`, `parameters[{code, paramType}]`), `tables` (calcRoutine, when bound), `binds`
(calcRoutine: `parameter`, `expression`, `type`, `kind`, `verify`, `from`, `at`).

**Reads** are one of `{entity, property}`, `{clausePattern, covTermPattern}`, `{modifierPattern}`,
`{modifierDataType}`, `{rateFactorPattern}`, or `{unit, value}` for a scratch value another unit
produces, each with `at`.

**Dataflow facts** (gosuMethod units). What the unit's body and the helpers it folds read and
write outside the ratebook routine, one row per distinct fact at its first occurrence, each with
`at`:

| List | Row | Holds |
| --- | --- | --- |
| `entityReads` | `{entity, property}` or `{entity, method, declaredIn}` | A property read on a captured entity (its supertypes and implemented entities included), with the receiver's entity; or a call of a gsrc enhancement method of the entity whose body only reads. |
| `engineMembers` | `{name, access, declaredIn: {class, at}}` | A read, write (`=`) or call of a member of the engine class or a base class under `gsrc`: fields, properties and functions that are not traversed methods (`CostDatas`, `addCost`, `rateInParallel`, `RateCache`, `_linePatternCode`). |
| `entityWrites` | `{target, property}` or `{target, method, declaredIn}` | An assignment to a captured entity's property; or a call of a gsrc enhancement method of the entity whose body assigns through `this` or calls a method named `add…`, `remove…`, `set…`, `create…`, `update…` and the like (`PolicyLine.addRatingWorksheet`). |
| `beanInserts` | `{entity}` | A `new <Entity>(...)` of a captured entity or an `entity.` import. |
| `crossInstance` | `{kind, expression, element, loop?}` | `total`: any operation on an engine member's CostData list (`CostDatas.sum(...)`), which reaches every cost rated so far. `sibling` (or `total` for an aggregate such as `sum`, `partition`, `Count`): an operation on a collection whose element type is the element type of a loop the unit runs under, with that loop's `var` and `in`. |
| `cacheAccess` | `{member, kind, declaredIn?}` | `fxRate`: the engine's FX rate cache (`RateCache`, declared `PolicyPeriodFXRateCache`) and a CostData amount converted through it (`ActualAmountBilling`, a `LazyFXConversion` in `gw/rating/CostData.gs`); `rateBook`: `RateBook.selectRateBook` and an engine member declared `RateBook`. |
| `unresolved` | `{fact, expression, reason}` | Every expression whose fact the run cannot establish, with the kind it would be: an entity read or write the capture cannot type, a call of an entity method in no gsrc enhancement (`entityCall`), an identifier that is no local, parameter, member or traversed method (`engineMember`), a `new` of an unknown class (`beanInsert`), a static call into code the run does not read, or a method call or undeclared member on a gsrc class such as a rating DTO (`externalCall`), and a calc routine no ratebook folder binds (`calcRoutine`, on each unit that executes it). |

Members of another class's own object (a CostData or a wrapper the unit constructed) are not engine
members, and reads of an object that is not an entity (a DTO, a CostData) are not entity reads;
an FX-converted CostData amount is still `cacheAccess`.

**Emissions.** `unit`, `costData` (the class constructed), `cost` (the Cost entity), `rateMode`
(`sliceMode` when `rateSlice` reaches the unit, `windowMode` for `rateWindow`), `when` (the switch
case that selects this class, `<subject> == <literal>`, or-joined for fall-through), `key` (the
platform key dimensions the source resolves: a typecode literal or the expression the constructor
chain assigns; `CostCode: null` on a classic line), `unresolvedKey` (the dimensions the source does
not resolve, platform dimensions first and then the CostData subclass's own; `[]` when every one
resolves), `status` (only from the decisions file), `at`. A dimension is in `key` or in
`unresolvedKey`, never both.

## What is mechanical

- **Traversal scope**: the selected engine's own file plus every `.gs`/`.gsx` file under that
  file's directory and its subdirectories. It never follows a call into `gsrc/gw/rating/` (the
  platform); a platform member is a locator, never a unit.
- **Dispatch**: the registered `IRatingPlugin` class; every `createRatingEngine` branch in any
  `*PolicyLineMethods.gs` / `*PolicyLineMethodsBase.gs` file that mentions the line, with its
  `RateMethod` and gate verbatim; the selected engine's base class; every `override` member with
  its baseline status.
- **Unit grain**: a reachable method is a unit when its own body constructs a CostData, calls
  `executeCalcRoutine`, or is scoped to at least one captured clause pattern by parameter type or
  switch case; or, when no unit sits between the roots and it, when it or a non-unit callee
  consults a `RateAdjFactorSearchCriteria`, reads a `<param>.<TermCode>Term`, or has a dataflow
  fact. A root (`rateSlice`, `rateWindow`, `rateOnly`) is a unit when its own body does any of
  those, and does not count as a unit between. So no fact is left without a unit to carry it: a
  dispatcher such as CP's `rateLocation`, which calls `addCosts` inside the parallel location loop,
  is a unit. A method with none of those folds into its caller: its call sites nest under the edge
  that calls it, and its facts roll up into the unit whose closure passes through it. Anonymous
  implementations use a location-derived `@anonymous-L<line>-C<column>` identity under their
  lexical owner.
- **Routine codes** (`kind: calcRoutine`, key = the code) resolve in three forms, never from a bare
  variable name: a literal string argument to `executeCalcRoutine`; a call to a method in the
  traversal set whose body only returns string literals (one routine per literal, scoped to the
  `switch` case labels in effect at that `return`); a local assigned from `<MapName>.get(...)` on a
  class-level `Map<Class<T>, String>` literal (one routine per entry, scoped to the key type).
  Anything else is an open question. A calc routine unit's `at` is the routine-code literal's
  declaration when one exists, else the `executeCalcRoutine` site.
- **Binds** (`calcRoutine` units only): the `Map<CalcRoutineParamName, Object>` literal reaching
  each `executeCalcRoutine` call, as an inline literal, a literal returned from a helper (one
  binding set per switch case), or a local mutated with `.put(TC_X, expr)` directly or through a
  one-hop helper. `TC_X` maps to the parameter code by lowercasing `X`, confirmed against
  `CalcRoutineParamName.tti`/`.ttx`. Each expression is typed the way the dataflow facts type a
  member chain (below), in the method whose text spells the map, to a captured entity, a
  CostData subclass, a class under `gsrc` or a Java scalar type (`kind` `entity`, `costData`,
  `gosuClass`, `scalar`); a string literal is a `String`; a `null` placeholder is `unbound`; a type
  that resolves but fits none of those (an entity the capture does not hold, a typelist, a list or
  a map) is `unclassified` with a `verify` note; anything unresolved is a `verify` note with no type.
- **`from`** (one line per bind): where the bound value comes from, followed from the expression's
  first identifier through locals (to their initializer or assignment) and method parameters (to
  the argument at the flow edge that reaches the method). The line names the kind and target the
  path stops at, the flow edge it was read at (`at <method key>:<line>`), the locals followed
  (`through a → b`), and the helpers that mutated the parameter map on the way (`mutated by
  <method key>:<line>, ...`, every helper passed that map whose body calls `.put(TC_X, ...)`):
  - `callResult <method key>`: a call to a traversed method;
  - `engineMember <name>`: a property or field of the engine class or a base class under `gsrc`
    (`CostDatas`, `PolicyLine`, `RateBook`);
  - `loopVariable <expression>`: a loop variable in effect at that statement;
  - `literal <text>`: a string, number, `null`, boolean or `TC_` literal;
  - `methodParameter <name>`: a parameter of a method no flow edge reaches;
  - `expression <text>`: after 4 hops, or an operator expression, a `new`, a call outside the
    traversed classes; the summary lists each one as an open question.
- **Ratebook digests**: `pins.ratebookFiles` lists `{path, sha256}` for every `book.json` read and
  every bound routine JSON, relative to the product root, sorted by path. The checkout files are
  identified by `revision` and `dirty`.
- **Reads**: `<param>.<TermCode>Term` on a clause-typed parameter, when the term code exists on
  that clause in the product capture, plus the reads a bound ratebook routine spells (below). Nothing
  else is inferred. Entity reads in method bodies are dataflow facts (`entityReads`), not `reads`.
- **Dataflow facts**: each identifier in a method body that is not after a `.` roots a member
  chain. Its type comes from the method's parameters, its locals (declared type, `new T(...)`,
  `x as T`, or the initializer's type), `for` and lambda variables (the element type of the
  collection they iterate), the members of the engine and its base classes (generic parameters
  substituted through each `extends` clause, so `PolicyLine` is the line's entity and
  `PolicyLineDTO` the line's DTO class), and the return type of a traversed method. A type name is
  a captured entity when it is one, when it is the line pattern code (its `policyLineSubtype`) or
  a clause pattern code (its `coverageSubtype`, `exclusionSubtype` or `conditionSubtype`). Each
  `.Member` on an entity is looked up in the capture: a foreign key or one-to-one continues on its
  target, an array on its element, `Branch` on the entity's `effDatedBranchType`, anything else
  ends the chain. A `.method(...)` on an entity is classified by its gsrc enhancement. String
  templates (`${...}`) are scanned; comments and string text are not. Static constants
  (`TC_…`, upper-case names) and type names used as values are not facts.
- **Source**: each `gosuMethod` unit's body, from the declaration line at its `at` to its closing
  brace, with the common indent removed, ending in one line feed.
- **Ratebook join** (`calcRoutine` units): the extractor reads every `<folder>/book.json` under
  `<product-root>/extracted/ratebooks` and searches only the folders whose `book.policyLine` is `--line` or
  `""` (a generic book).
  - A routine code whose `routines/<code>.json` is in exactly one of those folders binds. The unit
    gains `ratebook`: `book` and `edition` from `book.json`, `routine` (the routine JSON, relative
    to the product root), `text` (`routines/<code>.txt`, the readable algorithm), the `sha256` of
    the routine JSON's bytes, `parameterSet` and its `parameters` (`code`, `paramType`, in the
    routine's order); and `tables` (distinct operand `tableCode` values, by step order then operand order).
  - `writes`: one entry per distinct `inScopeValue` of an `assignment` step whose `inScopeParam` is
    `costdata`; an assignment to another writable parameter is a `scratch` entry `<param>.<value>`.
    Consultant `produces` rows are appended after them.
  - `reads`: one typed read per distinct in-scope value an operand or an operand argument reads from
    a parameter other than `costdata`. `inScopeValueIsModifier: true` gives `{modifierPattern}`. A
    `covTermCode` gives `{clausePattern, covTermPattern}` when the capture settles the clause (the
    parameter's `coveragePattern`, else the one captured clause declaring the term). A property of a
    parameter whose bind resolved to a captured entity gives `{entity, property}`. When that
    parameter is a wrapper (`useWrapper` with a `wrapperClass`), the read is what the wrapper's
    `property get` returns, one row per `case` or `return`: a `<field>.<Code>Term...` return is
    `{clausePattern, covTermPattern}` (the clause is the `case` pattern when it declares the term,
    else the one captured clause that does), a `<field>.<Property>` return is `{entity, property}`
    on the bound entity, and a literal return reads nothing. It is never an `{entity, property}` row
    naming the wrapped entity for the wrapper's own property: CPLine `cp_cov_premium_rr` reads
    `coverage.Limit` as `CPBPPCov`/`CPBPPCovLimit` and `CPBldgCov`/`CPBldgCovLimit`
    (`CPCoverageWrapper.gs:27-36`). A getter with any other return is an open question. Each `at` is the
    routine JSON relative to the product root plus `#step <order>` of the first step holding it. A
    bare parameter with no value is not a read; its bind already records it. Anything else is an
    open question. Consultant reads are appended after them.
  - Reported, never guessed: a routine in no searched folder stays unbound with no `ratebook`; a
    routine in more than one folder binds none and the summary names every candidate folder; a
    bind the parameter set does not declare, and a declared parameter no bind binds, are open
    questions.
- **Lookups**: one per `RateAdjFactorSearchCriteria` factor name, found directly or through helper
  methods that forward a String parameter into the criteria. Dimensions are the populated columns
  across that factor's rows; rows are never read.
- **Emissions**: one per (unit, CostData class constructed, switch case). The Cost entity comes
  from `createCostDataForCost`'s switch or the class's `extends …<Cost>` declaration. When the
  construction sits under `switch (<param>)` in the unit, `when` is that case, and every edge that
  passes a string literal for that parameter carries `emits` for the class the literal selects. A
  key dimension gets a value only when the CostData constructor chain assigns it a typecode
  literal or an expression the parser can read, or the emitting call passes one into a constructor
  parameter the chain assigns to that dimension.
- **Flow**: the engine's `rateSlice` and `rateWindow` bodies are walked statement by statement, and
  each called method's body is walked under that edge until a unit is reached.
  - An edge is recorded when its target is a unit, or a method whose own call sites reach a unit.
    Logging, platform calls and filters are not recorded.
  - A calc routine execution is a `routine` edge nested under the method that calls
    `executeCalcRoutine`. A routine code passed in as a parameter resolves through the argument the
    caller passed at the edge above, then through locals and the literal-returning helpers listed
    under Routine codes.
  - `when` is relative to the method body the call sits in, outermost first: `if (x)` gives `x`;
    an `else` gives `not (x)`; an `else if (y)` gives `not (x)` then `y`; `case A:` gives
    `<subject> is A` (fall-through labels join as `<subject> is A or B`); `default` gives
    `<subject> is none of the cases`; a `catch` gives `on exception <declaration>`. A `continue`,
    `return` or `throw` under guard `g` adds `not (g)` to every later statement in the same block.
  - `for` is relative to the same body, outermost first: `for (x in list)`, `list.each(\ x -> ...)`
    (and the other block-lambda iterators `eachWithIndex`, `forEach`, `map`, `where`, `firstWhere`,
    `hasMatch`, `allMatch`, `sum`), and `rateInParallel(list, \x -> ...)` with `parallel: true`. A
    `while` loop is not recorded. The loop's collection is typed the way the dataflow facts type
    a chain; `entity` names the captured entity it holds, `class` the gsrc class (a rating DTO's
    `List<CPLocationDTO>` gives `CPLocationDTO`); when neither resolves, the loop carries a
    `verify` note instead and the summary lists it as an open question.
  - A call nested in another call's argument list is recorded as its own edge, before the enclosing
    call. A target already on the current call path gets no nested edges.
- **Baseline status** per member: `vanilla` when the method body matches `modules/base.zip` after
  whitespace and comment normalization, `customized` when it differs, `noBaseline` when the
  baseline lacks the file or member (and for every calc routine, which lives in the database).
- **Cross-document references**: every clause pattern and entity a unit or emission names is
  checked against the product capture. A gap is reported, never filtered out of the document.
- **Zero units is a refusal**: a run whose traversal finds no unit exits 2 naming the engine and
  the roots it defines, instead of writing an empty document.

## The decisions file

`<product-root>/decisions/rating-process-<EngineClass>.yaml`, passed as `--decisions`, one per
engine. It is YAML in a subset the extractor reads with its own standard-library reader: comments,
block maps and lists, keys bare or double-quoted (a unit or emission key holds dots, so it is
quoted; a quoted key holds no backslash), double-quoted strings whose only escapes are `\\` and
`\"`, integers, `true`, `false`, `null`, `[]` and `{}`. An unquoted
string, a flow list or map, or anything else outside the subset stops the run with the file and
line. Every key is optional; `--engine` wins over `selectedEngine`; an unknown top-level key is
refused. Every locator in it is an `at`: one
string `<path>[:<line>][#<symbol>]` (the path relative to the source root, no whitespace, a
1-based line), or a list of them; the script checks each against that grammar. A locator written
under any key but `at` is refused.

| Key | Holds |
| --- | --- |
| `selectedEngine` | The `createRatingEngine` branch the document describes (or pass `--engine`). |
| `assumedGates` | The value assumed for each gate on the way to the selected branch: exactly `{name, value, at}`; `at` may be a list. |
| `units.<key>.reads` | Reads the source does not spell as `<param>.<TermCode>Term`, in one typed form (`{entity, property}`, `{clausePattern, covTermPattern}`, `{modifierPattern}`, `{modifierDataType}`, `{rateFactorPattern}`, or `{unit, value}`), each with `at`. Appended to the mechanical reads, never replacing them. |
| `units.<key>.produces` | What a unit writes that neither the code nor a bound ratebook routine spells: `{kind: costDataMember, name}` lands in `writes`, `{kind: scratchValue, name}` in `scratch`. Appended to the mechanical entries. |
| `emissions.<unit>.<CostDataClass>` | `key`: a map of dimension to value (a string, or `null` when the source establishes the dimension is unset), merged over the mechanical dimensions; `status`: `liveEngineEmitted` or `conditionallyEmitted`. |

Unit keys are spelled exactly as the document's `units[].key`; an emission key is its `unit` and
`costData` joined by a dot.

```yaml
selectedEngine: "CPRatingEngine"
assumedGates:
  - name: "PCConfigParameters.EnableCPDTOParallelRating"
    value: "false"
    at:
      - "modules/configuration/gsrc/gw/lob/cp/CPPolicyLineMethods.gs:287"
      - "modules/configuration/config/config.xml#EnableCPDTOParallelRating"
  - name: "RateMethod"
    value: "not TC_SYSTABLE"
    at: "modules/configuration/gsrc/gw/lob/cp/CPPolicyLineMethods.gs:284"
emissions:
  "CPRatingEngine.createCostData.CPBuildingCovGroup1CostData":
    key:
      ChargePattern: "TC_PREMIUM"
      ChargeGroup: null
    status: "liveEngineEmitted"
```

The extractor reads the ratebook folders, never the export XML. A calc routine with no bound
ratebook routine stays an open question until `pc-ratebook-extraction` writes its book folder
under the product root and the extractor re-runs.

## Sibling skills

`pc-product-model-extraction` writes the product capture, the prerequisite: it supplies the
identifiers this document joins to and the types its facts are read with. `pc-ratebook-extraction`
turns a ratebook export into the `extracted/ratebooks/<book>-<edition>/`
folders under the same product root, which hold the calc routines, their readable text and the
rate tables the `calcRoutine` units bind to.
