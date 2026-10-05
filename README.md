# pc-tools-public

Agent skills and scripts for working with PC rating worksheets. Each skill is a
self-contained directory under `skills/` with a `SKILL.md` for Claude Code or
Codex, a `README.md` reference, a `PROMPT.md` for use without the skill, and its
scripts.

## Worksheet pipeline

The three skills form one pipeline for comparing two rating runs of the same job
and quote branch:

| Step | Skill | Input | Output |
|---|---|---|---|
| Extract | [`pc-worksheet-extraction`](skills/pc-worksheet-extraction/README.md) | Job number and a read-only SQL Server login | The retained worksheets XML, byte for byte |
| Normalize | [`pc-worksheet-normalization`](skills/pc-worksheet-normalization/README.md) | Worksheets XML | Final-values v2 JSON |
| Compare | [`pc-worksheet-comparison`](skills/pc-worksheet-comparison/README.md) | Two final-values v2 JSON files | Comparison v2 JSON and a Markdown report |

The database keeps only the latest rating data for a job, so save each run's XML
before rerating if you want to compare it later.

Synthetic worked examples live in [`examples/synthetic/worksheets/`](examples/synthetic/worksheets/README.md).

## Requirements

- Python 3.8 or later. Normalize and compare use the standard library only.
- Extract also needs `pymssql`; see its [README](skills/pc-worksheet-extraction/README.md).
- Node 24 or later to run the test suite.

## Install

```bash
./install.sh
```

This links each `skills/<name>/` directory into `~/.claude/skills`. Use
`./install.sh --into <repo>` to install into one repository, and
`./install.sh --uninstall` to remove the links. Codex discovers the skills in
`.agents/skills/` when you open this repository.

## Test

```bash
npm test
```

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
