# Repository instructions

## Operating rules

- Never test changes in a browser. The operator validates changes and reports problems.
- Use pnpm and Docker when they are needed.
- Create commits as `Mário Veronesi Medina <mazen.mario@gmail.com>`.

## Scientific invariants

- This project extends ASVspoof 5 Track 1 only. Track 2 ASV enrollment and trial material is out of scope.
- Treat the unpacked ASVspoof 5 files as immutable source data. Preserve original audio, filenames, train/development/evaluation partitions, protocol rows, labels, and metadata.
- Add presentation-channel metadata alongside the source annotations; never replace the ASVspoof 5 annotations.
- Keep bona fide and spoof source material represented in playback acquisition to avoid a channel/class shortcut.
- Do not select samples or create re-recording scripts unless the task explicitly starts that later phase.

## Context routing

Load only the context relevant to the task:

- `.agent-context/current-state.md`: preparation status and decisions already made.
- `.agent-context/methodology.md`: full scientific methodology, labels, acquisition design, and evaluation protocols. Read it before changing selection, metadata, naming, playback conditions, or partitions.
- `original-asvspoof5/README.txt`: upstream layout, field definitions, license notes, and citation.
- `original-asvspoof5/PROVENANCE.md`: upstream Git revision, integrity checks, and retained Track 1 counts.

The `.agent-context/` directory and `methodology.tex` are intentionally ignored by Git and must not be distributed.

