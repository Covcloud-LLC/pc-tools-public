# pc-ratebook-extraction

Turns one exported PC Rate Management (RTM) **ratebook** into an analysis set a human or
an LLM can read: JSON for the book, its rate tables and its calc routines, a plain-text rendering
of each routine, and a CSV of each table's rows. It prints a summary for the human gate.

It is not the product-model capture or the rating-process capture. Those two are witnesses and
never carry a formula, rate, factor value, or rounding rule. A ratebook is all four of those
things, so its extraction has its own folder under `extracted/ratebooks/`, and nothing here writes
the product capture or a rating-process document. `scripts/ratebook-extract.py` is Python 3.8+, standard library only, with no
network. The workflow is in [PROMPT.md](PROMPT.md).

There is no decisions file. A ratebook export is self-describing: every value in the output is
copied from the file, so the extractor runs on its own and the judgment lands in the summary's
open questions.

## Running it

```sh
python3 skills/pc-ratebook-extraction/scripts/ratebook-extract.py \
  --ratebook     path/to/RateBookExport.xml \
  --product-root /path/to/<slug>
```

| Flag | Meaning |
| --- | --- |
| `--ratebook` | The export XML. Required. Its root must be `<import xmlns="http://guidewire.com/pc/exim/import">`. `book.json` records its file name and sha256. |
| `--product-root` | Product root; supplies the default for `--out` as `<root>/extracted/ratebooks/<book-code>-<edition>` — a folder named from the book code and edition the export itself carries, derived only once the export is parsed. An explicitly passed `--out` always wins. |
| `--out` | Directory to write into. Required unless `--survey` or `--product-root`. |
| `--steps` | How much of each calc routine the routine JSON carries: `full` (default), `summary`, or `none`. See below. |
| `--rows` | How much of each rate table the CSVs carry: `all` (default), `sample`, or `none`. See below. |
| `--row-limit` | Rows per table under `--rows sample`. Default 500. Under `--rows all` it cuts nothing and only decides which tables the summary names as large. |
| `--force` | Rewrite a non-empty `--out`. Without it a non-empty directory is refused. |
| `--survey` | List every element in the export and whether the extractor carries it. Writes nothing, needs no `--out`. Run this first against an unfamiliar export. |

Exit codes: 0 written (or surveyed), 2 refused. It refuses a root element outside the import
namespace, a malformed export, and a non-empty output directory without `--force`.

With `--product-root`, the extractor parses the export first to read the book's own code and
edition, then writes into `<root>/extracted/ratebooks/<book code>-<edition>/` — so two different editions of
the same book, or two different books, each land in their own self-named sibling folder under one
product root, without the caller having to spell out a book-specific `--out` path.

## Choosing a step detail

A calc routine's operand tree is most of the output. In the shipped sample book the largest
routine, `pa_veh_cov_premium_rr`, is 32 steps and 2,019 lines of JSON, of which 1,779 lines are the
steps — the other 240 are the routine's identity, parameters and references. A client book with
several routines that size will not fit comfortably in a reading session or an LLM context window.

`--steps` sets how much of that the routine JSON carries. Same book, same routine:

| `--steps` | Lines | What the JSON holds |
| --- | --- | --- |
| `full` (default) | 2,019 | Every step with its whole operand tree: operand order and type, operator, constants, table code, returned factor columns, arguments and their overrides, rounding scale, parentheses. |
| `summary` | 499 | One entry per step: order, step type, target, the same expression text the `.txt` renders, the tables it consults, and its notes and store type when it has them. Enough to read the algorithm and grep it by table; not enough to reconstruct an operand. |
| `none` | 240 | No steps. The routine's identity, its parameter set, and `references` — the tables, rate functions, modifiers, cov terms, in-scope values and local variables it reaches. |

Two things hold whichever you choose:

- **`routines/<code>.txt` is always written in full.** `--steps` governs the machine format only.
  So `--steps none` is not "throw the algorithm away"; it is "the text file is where I will read
  it." The summary's `expression` field and the `.txt` come from one function, so they cannot
  disagree.
- **`routine.stepDetail` in the JSON records which mode produced it**, so a thin document is never
  mistaken for a short routine. `stepCount` is the real count in every mode.

Start at `summary`. Drop to `none` when you only need the dependency graph, and re-run at `full`
for the one routine you are actually taking apart.

## Choosing a row detail

The other half of the output volume is the rates themselves. A territory-by-class table in a real
book runs to tens of thousands of rows; the shipped sample book's largest table is 12. `--rows`
sets how much of each table reaches the CSVs.

| `--rows` | What `tables/` holds |
| --- | --- |
| `all` (default) | Every row of every table, in `tables/<code>.csv`. |
| `sample` | At most `--row-limit` rows per table. The cut is taken after the sort, so the file is the deterministic head of the full table and two runs of the same export are byte-identical. |
| `none` | No row CSV at all. Nothing but the rows is dropped. |

Three things hold whichever you choose:

- **A file cut short is named `tables/<code>.sample.csv`, never `tables/<code>.csv`.** The suffix
  means exactly one thing: this file is not the whole table. A table that fits under the limit is
  complete, so it keeps the plain name — under `--rows sample` you can tell a whole table from a
  sampled one by its filename alone, without opening either.
- **The row count is complete in every mode.** `rowCount` comes from the parse, not from the CSV
  write, so `table.rows.count` and `book.json`'s per-table `rowCount` say what the table really
  holds even at `--rows none`; `rows.written` and `rows.truncated` say what this
  run put on disk. So do the keys, the factor columns, the physical-column map and the argument
  sources: `--rows` drops rates, never structure.
- **A cut table is an open question and an unchecked readiness item** in the summary, and
  `table.rows.detail` records which mode produced the folder — a sampled table can never be read as
  a whole one by accident. At `--rows all` the summary names the tables above `--row-limit`, so the
  run that carried the book whole tells you what a later sample run would cut.

Start at `all`. Reach for `sample` when a book is too large to read or to fit in an LLM context,
and `none` when you want the structure, the counts and the dependency graph without the rates.

## What it writes

```
<out>/
  book.json               header, membership, per-table storage attributes, parameter sets, counts
  tables/<code>.json      key columns and their match ops, factor columns, the physical-column map,
                          argument source sets with the object-graph path each key binds to
  tables/<code>.csv       the rows, physical columns decoded to logical names, one file per table
  tables/<code>.sample.csv  written instead of <code>.csv when --rows sample cut that table short
  routines/<code>.json    parameters, what the routine references, and its steps at the
                          detail --steps asked for
  routines/<code>.txt     the same steps as readable pseudo-code
```

`book.json`'s `source` names the export by file name and sha256; the routine and table JSON carry
no copy of it. Each routine JSON's `references` lists the tables, rate functions, modifiers, cov
terms, in-scope values and local variables the routine reaches, and `book.json` lists each table's
`usedByRoutines`.

## Four things worth knowing about the format

**Rows are stored in anonymous physical columns.** A row backed by the shared `DefaultRateFactorRow`
entity is `str1..str8`, `int1..int8`, `dec1..dec6`, `date1..date2`, `bit1..bit2`, all empty but the
used ones. The `RateTableDefinition` is the only thing that says `str1` is `COV_CODE` and `dec5` is
`Base Rate`. Joining the two is what the CSV writer is for. A table backed by a dedicated extension
entity names its own columns instead, so the extractor reads every scalar child of a row rather
than a fixed slot list, and reports any populated column no definition claims.

**A calc routine is an operand chain, not a tree.** Each step carries a `StepType` and an ordered
operand list; a `continue` step extends the assignment above it. PC evaluates the chain
strictly left to right with no operator precedence, so the text rendering is flat and never adds
parentheses of its own. The parentheses a step's operand carries (`leftParenthesisGroup`,
`rightParenthesisGroup` in the JSON) are the routine's own grouping, and the text shows them as
exported, after the operator and around the operand: `× ("1"` then `+ policyline.CPScheduleCredits
[modifier])`.

**Values are never reformatted.** A decimal exported as `40.0000000` is written as `40.0000000`. A
rate that reads oddly is the client's rate.

**The text file is read-only.** It is for a human or an LLM reading a routine end to end. The JSON
beside it is the machine format; nothing parses the text back.

## The summary

The run prints a summary: the book, edition and output folder; routine, table, step and row counts;
`Elements not carried:` (every element name the extractor does not read, with its count);
`Open questions:`; and `Readiness:`, a checklist that is ticked only for what the run established.
The open questions are facts the export does not carry, each with the locator it names:

- a **custom match-op definition**, which is a `public-id` in the export and a class in the checkout
- **tables with zero rows**, which may be genuinely empty, held in another edition, or excluded
- **tables this run did not carry in full**, or did not carry at all, under `--rows`
- **argument sources with no binding path**
- **unmapped row columns**: a physical column rows populate that no column definition claims, so the CSV omits it
- a **table a routine looks up that the book does not carry**, and a **routine not attached** to it
- any **unrecognized step type, operand type, operator, or rounding scale** — carried verbatim, but
  the rendering may be wrong
- **localized labels**: a localization array with content, of which only the base label is written
