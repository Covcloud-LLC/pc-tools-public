You are reading a PC implementation checkout. Read-only. Write one Markdown
report, `RATING-ACCESS-REPORT-<Line>.md`, that another session will use to design a rating
"access map" (which objects the rating engine and rate routines read and write, and whether a
write reaches a database entity). Report facts with `path:line` in this checkout. Mark anything
you inferred as "inference". Do not paste source beyond one-line signatures. If a section cannot
be established, say so and why; never guess.

Pick one line of business: <Line>. If several rating engines exist for it, cover each.

## 1. Environment
- PC version, checkout revision or build label, SBT (Standards-Based Template) yes/no.
- Is DTO-based parallel rating used on any line? Which config parameters gate parallel rating
  (`ParallelizedRatingEnabled`, `MaxRatingThreadPoolSize`, per-line gates) and their values.

## 2. Engine inventory
- Product code, line code, dispatch class (`<Line>PolicyLineMethods` or equivalent) and the
  rating engine class(es). For each engine: the full base-class chain up to the platform class,
  with the path of each class or "in jar <name>".
- Which engine methods are the units of work (rateSlice, rateWindow, per-coverable methods).

## 3. The object tree the engine rates (LineModelObject or equivalent)
One table row per class in the wrapper tree:
| class | path | wraps entity | holds entity reference or copies values | setters (name → what they write, entity field or local) | built by (path:line) |
Then: is the tree built once per rating run or per slice; is it shared across threads.

## 4. Rate routine parameters
- Each parameter set used by the line's routines: parameter name → typecode → class or entity.
- Every routine store target (assignment instruction): routine, in-scope parameter, property.
  One table. Flag any store whose in-scope parameter is an entity or an entity-backed wrapper.
- The rate routine plugin class (`gw.plugin.rateflow`) and its cost-data wrapper classes.

## 5. Engine writes
Per unit of work: entity field writes (`entity.Field = …`), bean inserts (`new X(bundle)`,
`addToX`), method calls on entities or wrappers that may write, cost-data writes, and calls
into base classes or static utilities whose body you did not read. One table:
| unit | target object | field or method | backing: entity / wrapper / DTO / costData / local / unknown | path:line |

## 6. Reads of entity-backed fields
Per unit and per routine: entity-backed object and field read, including lookup arguments
(argument source sets). This list is the candidate DTO field set. One table.

## 7. Runtime evidence
- Are rating worksheets retained? Is SQL Server read access possible? One rated job number.
- If a worksheet can be read: for one cost, the object/property reads and their values.

## 8. Not established
List every call, class or value you could not resolve, with the reason.
