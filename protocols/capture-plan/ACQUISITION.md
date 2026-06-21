# Playback acquisition scripts

The acquisition workflow is split into a disposable pilot and a definitive
condition run. Both use the immutable capture plan; neither selects new
production assignments.

## Installation

Use native Windows Python rather than Docker so that PortAudio can access the
Windows audio devices and their drivers.

```powershell
python -m pip install -r requirements-capture.txt
```

The scripts require Python 3.11 or newer and load NumPy, SciPy, SoundDevice and
SoundFile only when hardware access or signal processing is requested.

## Device discovery

```powershell
python scripts/capture_test.py --list-devices
```

The table reports the transient PortAudio index, direction, host API and
Windows endpoint name. Prefer a unique device-name selector plus host API in
the configuration. Indices can change after USB reconnection or reboot.

The Yamaha HS5 and AT2020 are analog devices and are not visible to Windows;
the endpoint name normally identifies their audio interface. The operator must
confirm the physical speaker, microphone, cables, knob positions and placement
before a session starts.

Copy `config/acquisition.example.json` to an ignored condition-specific file,
for example `acquisition-config.HH.json`, and replace all placeholders. The
configuration condition must match the command condition. The later pilot can
also build this configuration from command-line device and setup arguments.

## Disposable pilot

The pilot selects a small reproducible random sample from the already assigned
condition jobs. It is stratified across `PH`/`PS` and all three source
partitions. The default is twelve jobs: two from each of the six
partition/category strata.

Generate only the selection, without audio dependencies or hardware:

```powershell
python scripts/capture_test.py --condition HH --count 12 --plan-only --clean
```

Run the pilot with an existing configuration:

```powershell
python scripts/capture_test.py `
  --condition HH `
  --count 12 `
  --config acquisition-config.HH.json `
  --clean
```

Or provide the most important equipment fields directly and save the resulting
configuration:

```powershell
python scripts/capture_test.py `
  --condition HH `
  --count 12 `
  --input-device "Focusrite USB Audio" `
  --output-device "Focusrite USB Audio" `
  --input-host-api "Windows WASAPI" `
  --output-host-api "Windows WASAPI" `
  --recording-equipment "Audio-Technica AT2020" `
  --playback-equipment "Yamaha HS5" `
  --audio-interface "Focusrite model and serial" `
  --distance "1.0 m" `
  --speaker-volume "marked knob position" `
  --microphone-gain "marked knob position" `
  --save-config acquisition-config.HH.json `
  --clean
```

`--clean` is deliberately restricted to `capture-tests/<condition>` and can
never delete `playback_flac`, source FLACs, protocols or another condition.
Without `--clean`, the script refuses to overwrite an existing pilot.

Pilot artifacts are local and ignored by Git:

```text
capture-tests/HH/
  test-plan.tsv
  selection.json
  acquisition-config.json
  session.json
  pilot-ledger.sqlite3
  attempt-results.jsonl
  recordings/<partition>/<PH|PS>/*.flac
  raw/<partition>/<PH|PS>/*.wav
  diagnostics/*.json
```

Raw captures and candidate final FLACs are retained for the pilot even when a
validation fails. Running again with `--clean` chooses a new random seed unless
`--seed` is specified.

## Definitive condition run

After approving and freezing the pilot configuration, verify the complete run
without opening devices:

```powershell
python scripts/capture_dataset.py `
  --condition HH `
  --config acquisition-config.HH.json `
  --dry-run
```

Check device resolution and the physical-chain confirmation without recording:

```powershell
python scripts/capture_dataset.py `
  --condition HH `
  --config acquisition-config.HH.json `
  --preflight-only
```

Execute all `HH` jobs, in train, development and evaluation order:

```powershell
python scripts/capture_dataset.py `
  --condition HH `
  --config acquisition-config.HH.json
```

Run the same command separately for `HL`, `LH` and `LL` with their approved
configuration files. The script verifies the three shard hashes before a run,
uses every row in those shards and writes only the row's authoritative
`output_audio_path`:

```text
playback_flac/<partition>/<condition>/<PH|PS>/<output_file_name>
```

One persistent full-duplex PortAudio stream remains open for the whole process;
the recorder does not reopen the Windows input/output endpoints for every job.
Input and output selectors must therefore resolve through the same host API,
normally Windows WASAPI.

Progress is committed after every job to
`capture-ledgers/<condition>/capture-ledger.sqlite3`. A rerun skips completed
jobs whose final file exists. Failed jobs are attempted according to the fixed
configuration and can later be explicitly retried with `--retry-failed`.
Successful production captures do not retain the raw WAV. A failed capture
keeps its raw WAV and a JSON diagnostic under `capture-failures/<condition>/`.

Once a definitive condition has started, its ledger refuses sessions whose
configuration hash differs from the first session. The executor also stops
after the configured number of consecutive failed jobs, preserving progress
instead of continuing through a disconnected or broken acquisition chain.

## Signal handling

The scripts do not normalize playback or change gain per file. Mono source
samples are duplicated across the configured output stream channels when the
endpoint is stereo. Acquisition includes fixed pre-roll and post-roll.

Alignment uses the controller timing only to constrain a content-based search:

1. coarse normalized cross-correlation at a lower analysis sample rate;
2. fine normalized cross-correlation around the coarse result, using the
   highest-energy reference segment;
3. a final mono segment with the same documented temporal duration as the
   source;
4. deterministic resampling to the configured release sample rate.

For every successful production job, the ledger retains the alignment offset,
alignment score, amplitude delta, frame counts, callback status and output
SHA-256. Detailed diagnostics and raw audio are reserved for pilot runs and
failures.

The amplitude bounds are `null` in the example. Use the pilot results to define
condition-specific fixed bounds if desired, then freeze the configuration. The
definitive recorder reports and validates; it never changes speaker volume or
microphone gain automatically.
