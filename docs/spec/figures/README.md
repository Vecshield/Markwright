# Figures

`owm-er.tex` is the OWM 0.1 entity-relationship diagram in Chen notation,
written in TikZ so it typesets natively in a paper.

```bash
tectonic owm-er.tex      # or: pdflatex owm-er.tex
```

For a paper, drop the `\documentclass`/`\begin{document}` wrapper and keep the
`tikzpicture` environment; it needs only `positioning`, `shapes.geometric` and
`calc`. The committed `owm-er.pdf` and `owm-er.png` are the rendered output.

The formal model this figure summarises (value domains, the integrity
constraints, lifecycle transition systems, the verification judgment) is in
[../data-model.md](../data-model.md).
