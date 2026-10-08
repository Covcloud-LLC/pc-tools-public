# pc-rating-workspace-extraction

Writes one PC line's **rating workspace**: the objects its calc routines take as input
and write as output. For each object it records what backs it (an entity, a wrapper over an
entity, a rating DTO, a class, or the generic cost data wrapper), where it sits under the line
object in the data model, and each property a routine reads or writes, with its declared type and
the file and line that declare it.

One mechanical pass: no decisions file and no human gate. What the inputs do not establish is
listed under `unresolved` for normalization to settle. `scripts/rating-workspace-extract.py` is
Python 3.8+, standard library only, with no network. The workflow is in [PROMPT.md](PROMPT.md).

## Inputs

All read-only. The product root is `<family>/<Line>/`, for example `work/pc/CPLine`.

| Input | What is read |
| --- | --- |
| `<product-root>/extracted/ratebooks/*/` | Every folder `pc-ratebook-extraction` wrote for the line, generic books included: `book.json`, the routine JSON (parameter set, steps) and the table JSON (keys, argument source sets). A book must be for the capture's line or for no line. |
| `<product-root>/extracted/product-capture.json` | `pc-product-model-extraction` output with `state: ready`: entities, their subtypes, delegates and arrays, coverage and cov term patterns, modifier patterns. |
| The PC checkout | At the capture's `source.revision`. Entity metadata (`modules/configuration/config/metadata/entity`, `config/extensions/entity`), enhancements and classes under `modules/configuration/gsrc`. |
| `admin/lib/pc-<version>.jar` in the checkout | When present: the sources of platform classes that `gsrc` does not hold, such as the rating DTO base classes `CoverageDTO`, `EffDatedDTO` and `PolicyLineDTO`. |

The rating-process document and the engine code are not inputs.

## Run

```sh
python3 skills/pc-rating-workspace-extraction/scripts/rating-workspace-extract.py \
  --product-root work/pc/CPLine \
  --source-root /path/to/pc-checkout
```

| Flag | Meaning |
| --- | --- |
| `--product-root` | Product root holding `extracted/product-capture.json` and `extracted/ratebooks/`. Required. |
| `--source-root` | The PC checkout. Required. Its configuration module is `modules/configuration`. |
| `--out` | Output directory. Default `<product-root>/extracted`, beside the capture. Refused inside the checkout or `extracted/ratebooks/`. The extractor writes only `rating-workspace.json` there and leaves every other file. |
| `--no-platform-jar` | Do not read the platform jar even when it is present. |
| `--overwrite` | Replace an existing `rating-workspace.json`. Without it an existing file is refused. |

Exit 0 writes the document and prints a summary: the saved path, the counts, and one line per
unresolved subject with its reason and first locator. Exit 2 prints
`{"status": "refused", "error"}` on stderr and writes nothing, for: a missing input, a capture
that is not `ready`, a source root that is not the top of a Git checkout, a checkout at a
different commit from the capture's `source.revision`, no ratebook folder, a ratebook folder for
another line, a routine extracted without `--steps full` (`stepDetail` other than `full`), more
than one `admin/lib/pc-<version>.jar`, a ratebook file that is not JSON, an output directory
inside the checkout or the ratebook folders, or an existing output without `--overwrite`. Two runs over the same inputs write byte-identical files.

## Output

`<out>/rating-workspace.json`.

```text
formatVersion   pc-tools.rating-workspace/1.0.0
product, line   from the capture's scope
source          revision (checkout commit, equal to the capture's), dirty, configurationRoot
inputs          productCapture {path, revision}; ratebooks [{folder, book, edition, export, sha256}];
                platformJar {path, sha256}, or null when no property or parent came from it
root            key of the line object
counts          objects, properties, scalars, unresolved
objects[]       key, kind (entity | wrapper | dto | gosuClass | costData), className, entity,
                subtype {entity, coveragePatterns, at} or null,
                parent (object key, null for the root), parentVia {kind, field, at} or null,
                parameters [{book, parameterSet, parameter, paramType}],
                properties [{path, type, typeFrom {kind, at}, exportTypes, modifier, covTerm,
                             readBy, writtenBy}]
scalars[]       {book, parameterSet, parameter, type, routines}
unresolved[]    {subject, reason, at[]}
```

Names, types and paths are PC's own. `type` is the declared type verbatim
(`BigDecimal`, `typekey.FireProtectClass`, `entity.VehicleDriver`, `rate`). `readBy` and
`writtenBy` hold routine codes. Locators: `capture` gives the capture's source reference
(`<file>#<symbol>`), `checkout` gives `<file>:<line>`, `platformJar` gives
`<jar>!/<entry>:<line>`, and `export` gives the ratebook JSON file and step.

### Objects

- One object per distinct parameter across every routine's parameter set: same parameter code and
  same backing class. Its `parameters` list every parameter set that supplies it.
- The **line object** is the root. Every parameter typed as the line entity or one of its
  supertypes (for example `entity.PolicyLine` in a generic book), or as a DTO class extending
  `gw.api.rating.dtobased.data.PolicyLineDTO`, is that one object. DTO line parameters of one class
  are one object whatever their codes.
- A parameter with `useWrapper` is a `wrapper` object backed by its `wrapperClass`, with `entity`
  naming the wrapped entity.
- A `gw.api.rating.dtobased.*` parameter is a `dto` object. Another non-entity class is a
  `gosuClass` object. `typekey.*`, `java.*` and primitive parameters are `scalars`.
- A parameter set with `includesCost` gives one `costdata` object backed by
  `gw.rating.flow.domain.CalcRoutineCostData`.
- A coverage parameter is narrowed to one coverage subtype (`subtype`) when its `coveragePattern`
  names one, or when every coverage pattern its wrapper's constructor accepts (`case X:` labels
  that are the line's coverage pattern codes) maps to the same `coverageSubtype`.

### Nesting

An object's parent comes from the data model only, never from how an engine iterates:

- An entity or wrapper object walks up the capture's arrays (an entity whose array holds this
  entity) level by level until a level reaches another object. When no array connects it, it walks
  up the captured foreign keys it and each entity above it declare instead, and `parentVia.kind` is
  `foreignKey`. A wrapper sits where its (narrowed) entity sits.
- A DTO object walks up the collection fields (`List<X>`, `X[]`) of the classes in its package.
- A class met on the way that backs no object becomes an object with no parameters (for example
  `CPLocation` between `building` and `policyline`).
- When the nearest level reaches more than one object, or nothing connects the object to the line
  object, its parent is null and `unresolved` holds `object:<key>`. The `costdata` object and `gosuClass`
  objects are always listed this way.

### Properties

A property is each path a routine reads or writes on an object, spelled as the export spells it
after the parameter name: step targets (`writtenBy`), in-scope operands and step arguments, and the
argument-source paths of each table the routine uses (`readBy`). For a table lookup the extractor
uses the argument source set with the operand's code that is bound to the routine's parameter set,
less the keys the step overrides. When no such set exists and some key is not overridden,
`unresolved` holds `argumentSources:<routine>:<table>:<set>`.

A path's type is found by walking its segments from the object's backing class:

1. On an entity: the capture's declarations, then the checkout's entity metadata for entities the
   capture does not hold, following supertypes and delegates; then enhancements of the entity and
   its supertypes. A foreign key or one-to-one continues the walk into its target entity.
2. On a class: the class's own `property get` and `var ... as` declarations, then the classes it
   extends, from `gsrc` first and the platform jar second.
3. A modifier read whose first segment is a modifier pattern takes the pattern's
   `modifierDataType`. A `<code>Term` segment for a cov term pattern stops the walk: its type is
   generated from the product model.
4. If no declaration is found and the export recorded exactly one type for the path
   (`storeType` or `inScopeValueType`), that type is used with an `export` locator.
5. Otherwise `type` and `typeFrom` are null and `unresolved` holds `property:<key>.<path>`.

`exportTypes` lists every type the export recorded for the path. `modifier` is true when any read
is a modifier read. `covTerm` holds the cov term code a step named for the path.
