# ASVspoof 5 Playback-Controlled Extension

This repository is the working source tree for a playback-controlled extension of ASVspoof 5. The intended dataset studies speech liveness under a presentation-constrained threat model: an attacker cannot inject digital audio and must reproduce a signal through a loudspeaker for microphone recapture.

The project contains the prepared, unchanged ASVspoof 5 Track 1 source material
and a versioned plan for assigning every retained source to one playback and
recapture condition. Physical acquisition has not started.

## Intended task

The extension separates content origin from presentation channel:

- `CH`: clean human speech, used as the live class;
- `PH`: bona fide source speech after playback and recapture;
- `PS`: spoof source speech after playback and recapture.

The main liveness task is `CH` versus `{PH, PS}`. Original ASVspoof 5
annotations remain authoritative and unchanged; the capture plan adds the
channel-related fields needed for the future derived dataset.

## Prepared source snapshot

Only Track 1 material is retained:

| Partition | Directory | Protocol rows / FLAC files |
| --- | --- | ---: |
| Train | `flac_T/` | 182,357 |
| Development | `flac_D/` | 140,950 |
| Evaluation | `flac_E_eval/` | 680,774 |
| **Total** |  | **1,004,081** |

Track 2-only enrollment audio and Track 2 protocols were excluded. The original partition names and FLAC filenames were not changed.

## Repository layout

```text
flac_T/                    ASVspoof 5 training audio
flac_D/                    ASVspoof 5 Track 1 development audio
flac_E_eval/               ASVspoof 5 Track 1 evaluation audio
original-asvspoof5/        upstream documentation and provenance
  protocols/               unchanged Track 1 protocols and codec table
protocols/capture-plan/     capture plan, acquisition guide, schema, and job shards
config/                     acquisition configuration template
scripts/                    capture-plan generator and acquisition executors
LICENSE.txt                upstream license text
```

The root documentation describes this extension. Upstream ASVspoof 5 documentation is preserved under `original-asvspoof5/` so future release files can be developed without overwriting the source records.

## Playback capture plan

The distributable capture plan is indexed by
[`protocols/capture-plan/capture-plan.json`](protocols/capture-plan/capture-plan.json).
It selects all 1,004,081 retained Track 1 sources and assigns each to exactly
one of `HH`, `HL`, `LH`, or `LL`. Assignments are deterministic, stratified,
and keep all codec variants sharing one `CODEC_SEED` in the same condition.

| Condition | Capture jobs |
| --- | ---: |
| `HH` | 251,026 |
| `HL` | 251,015 |
| `LH` | 251,019 |
| `LL` | 251,021 |

The plan contains 188,819 `PH` jobs from bona fide sources and 815,262 `PS`
jobs from spoof sources. Each bona fide source remains the unchanged `CH`
reference as well as supplying its `PH` capture job; no duplicate clean-audio
job is created.

Job rows are stored as twelve streamable TSV shards by source partition and
playback condition. The index records the labels, destination paths, equipment
quality mapping, allocation algorithm, counts, and a SHA-256 for every shard.
See [`protocols/capture-plan/README.md`](protocols/capture-plan/README.md) for
the recorder contract and field semantics.

Native Windows Python scripts now implement a disposable stratified pilot and
the resumable definitive recorder for one condition at a time. Device binding,
content-based alignment, FLAC generation, validation, failure diagnostics and
the SQLite progress ledger are documented in
[`protocols/capture-plan/ACQUISITION.md`](protocols/capture-plan/ACQUISITION.md).

## Integrity and provenance

All 18 upstream audio archives matched the MD5 checks published by ASVspoof 5 before extraction. The source Git revision, initial import commit, exclusions, and exact counts are recorded in [`original-asvspoof5/PROVENANCE.md`](original-asvspoof5/PROVENANCE.md).

## License and attribution

The retained source material remains subject to the ASVspoof 5 licensing, attribution, ethics, and citation requirements. See [`LICENSE.txt`](LICENSE.txt) and [`original-asvspoof5/README.txt`](original-asvspoof5/README.txt).

The exact equipment bindings and fixed setup must still be approved through
pilots for `HH`, `HL`, `LH`, and `LL`. No physical recaptured audio or final
derived release protocol has been produced yet.
