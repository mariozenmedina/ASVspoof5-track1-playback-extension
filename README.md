# ASVspoof 5 Playback-Controlled Extension

This repository is the working source tree for a playback-controlled extension of ASVspoof 5. The intended dataset studies speech liveness under a presentation-constrained threat model: an attacker cannot inject digital audio and must reproduce a signal through a loudspeaker for microphone recapture.

The project currently contains only the prepared, unchanged ASVspoof 5 Track 1 source material. Sample selection, condition assignment, playback/recapture, derived metadata, and release protocols have not been created yet.

## Intended task

The extension separates content origin from presentation channel:

- `CH`: clean human speech, used as the live class;
- `PH`: bona fide source speech after playback and recapture;
- `PS`: spoof source speech after playback and recapture.

The main liveness task is `CH` versus `{PH, PS}`. Original ASVspoof 5 annotations remain authoritative and will be preserved unchanged; the extension will add new channel-related fields later.

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
LICENSE.txt                upstream license text
```

The root documentation describes this extension. Upstream ASVspoof 5 documentation is preserved under `original-asvspoof5/` so future release files can be developed without overwriting the source records.

## Integrity and provenance

All 18 upstream audio archives matched the MD5 checks published by ASVspoof 5 before extraction. The source Git revision, initial import commit, exclusions, and exact counts are recorded in [`original-asvspoof5/PROVENANCE.md`](original-asvspoof5/PROVENANCE.md).

## License and attribution

The retained source material remains subject to the ASVspoof 5 licensing, attribution, ethics, and citation requirements. See [`LICENSE.txt`](LICENSE.txt) and [`original-asvspoof5/README.txt`](original-asvspoof5/README.txt).

This README is intentionally preliminary and must be updated as the derived dataset, acquisition protocol, metadata, and distribution structure are finalized.

