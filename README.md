# pc-tools-public

Agent skills and scripts for working with a PC configuration and its rating:
extracting a line's product model, ratebooks, rating workspace and rating
process, and comparing rating worksheets. Each skill is a self-contained
directory under `skills/` with a `SKILL.md` for Claude Code or Codex, a
`README.md` reference, a `PROMPT.md` for use without the skill, and its scripts.

## Product and rating extraction

Four skills read one line of a PC 10.2.3 configuration checkout, and the
ratebooks exported from PC Rate Management, into JSON a person or an agent can
read. They share one product root per line: each writes under its
`extracted/` folder, and each later step reads what the earlier ones wrote.

| Step | Skill | Input | Output |
|---|---|---|---|
| Product model | [`pc-product-model-extraction`](skills/pc-product-model-extraction/README.md) | The checkout, a product and a line | `product-capture.json` |
| Ratebooks | [`pc-ratebook-extraction`](skills/pc-ratebook-extraction/README.md) | A ratebook export XML | One folder per book: JSON for the book, tables and routines, routine text, table CSV |
| Rating workspace | [`pc-rating-workspace-extraction`](skills/pc-rating-workspace-extraction/README.md) | The product capture, the ratebook folders and the checkout | `rating-workspace.json` |
| Rating process | [`pc-rating-process-extraction`](skills/pc-rating-process-extraction/README.md) | The checkout, the product capture and the ratebook folders | `rating-process-<EngineClass>.json`, one per rating engine |

## Worksheet pipeline

The three skills form one pipeline for comparing two rating runs of the same job
and quote branch:

| Step | Skill | Input | Output |
|---|---|---|---|
| Extract | [`pc-worksheet-extraction`](skills/pc-worksheet-extraction/README.md) | Job number and a read-only SQL Server login | The retained worksheets XML, byte for byte |
| Normalize | [`pc-worksheet-normalization`](skills/pc-worksheet-normalization/README.md) | Worksheets XML | Final-values v3 JSON |
| Compare | [`pc-worksheet-comparison`](skills/pc-worksheet-comparison/README.md) | Two final-values v3 JSON files | Comparison v3 JSON and a Markdown report |

The database keeps only the latest rating data for a job, so save each run's XML
before rerating if you want to compare it later.

## Requirements

- Python 3.8 or later. Every script except worksheet extraction uses the
  standard library only.
- Worksheet extraction also needs `pymssql`; see its [README](skills/pc-worksheet-extraction/README.md).

## Install

```bash
./install.sh
```

This links each `skills/<name>/` directory into `~/.claude/skills`. Use
`./install.sh --into <repo>` to install into one repository, and
`./install.sh --uninstall` to remove the links. Codex discovers the skills in
`.agents/skills/` when you open this repository.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
