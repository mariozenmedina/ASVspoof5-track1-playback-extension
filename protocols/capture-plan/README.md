# Playback capture plan

This directory defines which immutable ASVspoof 5 Track 1 files will be played
and recaptured, which playback condition applies to each file, and where the
resulting FLAC must be stored. It is a capture plan, not a capture-progress
ledger and not a recording script.

## Files

- `capture-plan.json` is the authoritative index. It records the selection and
  allocation rules, condition meanings, destination convention, counts, and
  SHA-256 digest of every job shard.
- `capture-plan.schema.json` describes one typed job row after a TSV row has
  been parsed. The two integer columns are decimal text in the TSV files.
- `jobs/<partition>.<condition>.tsv` contains the ordered capture jobs. The
  twelve shards keep files small enough to stream and allow acquisition one
  equipment condition at a time.

The index is JSON, while the million-row job table is sharded TSV. Repeating
JSON keys for every job would add substantial release size without adding
information. All files use UTF-8, LF line endings, and repository-relative
paths with `/` separators.

## Selection criterion

The plan selects every retained Track 1 protocol row: 182,357 train, 140,950
development, and 680,774 evaluation sources. No sampling budget was introduced
because the scientific methodology does not define one. Every selected source
has exactly one playback job and remains in its original ASVspoof 5 partition.

Every bona fide source therefore has two roles:

1. its unchanged source FLAC remains the clean-human (`CH`) reference;
2. the same FLAC is played and recaptured once to create a playback-human
   (`PH`) sample.

Every spoof source is a clean-spoof (`CS`) source reference and is played and
recaptured once to create an operational playback-spoof (`PS`) sample. The plan
does not create copy jobs for `CH` or operational jobs for `CS`.

## Condition assignment

The first condition letter describes playback-device quality and the second
describes recording-device quality:

| Condition | Playback quality | Recording quality |
| --- | --- | --- |
| `HH` | high | high |
| `HL` | high | low |
| `LH` | low | high |
| `LL` | low | low |

The methodology identifies the Yamaha HS5 as the high-quality playback
reference and the Audio-Technica AT2020 as the high-quality recording
reference. Exact device instances, low-quality devices, serial numbers, and
the audio interface or soundcard are deliberately deferred. The later capture
configuration must bind those items to `HH`, `HL`, `LH`, and `LL` without
changing the assignments in this plan.

Assignment is deterministic and source-family-aware:

- `CODEC_SEED` is the family identifier when present; otherwise the source file
  identifier is used;
- every codec version of one evaluation utterance stays in the same playback
  condition, preventing the same underlying utterance from leaking across
  conditions;
- families are stratified by partition, speaker, gender, `KEY`, attack label,
  attack tag, and codec-family shape;
- strata are processed in lexical order, families inside each stratum are
  ordered with SHA-256, and conditions are ordered by running attack, `KEY`,
  speaker, and partition job counts with a SHA-256 tie-break before round-robin
  assignment;
- the family-count difference between conditions is at most one in every
  stratum.

The exact seed and algorithm are recorded in `capture-plan.json`. The original
ten ASVspoof 5 fields are copied unchanged into every job row.

## Destination layout and filenames

Recaptured files are written under a new tree; source files must never be
overwritten:

```text
playback_flac/
  <train|development|evaluation>/
    <HH|HL|LH|LL>/
      <PH|PS>/
        <source_id>_<PH|PS>_playback_<condition>.flac
```

For example:

```text
playback_flac/train/HH/PS/T_0000000000_PS_playback_HH.flac
```

The path and filename are explicit in each job as `output_audio_path` and
`output_file_name`; filenames are only a convenience, and metadata rows remain
authoritative.

## Recorder contract

The acquisition scripts:

1. load `capture-plan.json` and verify the selected shard SHA-256;
2. require an acquisition configuration that resolves the selected condition
   to actual equipment and fixed room/gain/placement settings;
3. process rows in `capture_order` within a shard;
4. read only `source_audio_path` and write only `output_audio_path`;
5. preserve the row metadata when producing the eventual derived protocol;
6. keep capture status, retries, checksums, and measured output properties in a
   separate acquisition ledger rather than modifying this plan.

Output sample rate, channel conversion, calibration, trimming, interface, and
gain are not guessed here. They belong to the fixed acquisition configuration
that must be documented before recording starts.

The pilot and definitive Python implementations of this contract are
documented in [`ACQUISITION.md`](ACQUISITION.md).

## Reproduction

Regenerate the index and all job shards from the immutable source protocols:

```powershell
node scripts/generate-capture-plan.mjs
```

The generator verifies Track 1 protocol/audio cardinality, source-file
existence, metadata-family consistency, unique jobs and destinations,
condition-family integrity, required `PH`/`PS` representation, the schema
column set, final counts, and shard hashes.
