# ASVspoof 5 source provenance

This dataset was prepared from the official ASVspoof 5 mirror below before its Git history was replaced.

- Upstream repository: `https://huggingface.co/datasets/jungjee/asvspoof5`
- Upstream branch: `main`
- Upstream commit: `5d4b1565bc0e3e79343af0b5eacc0ea395405d59`
- Upstream tree: `3fdc8c95b992dfe5c7000e50053163e851d01af3`
- Parent commit: `2336727d5848e8c70072d06fa6b11bb6a5b4255c`
- Commit subject: `Upload flac_E_aj.tar with huggingface_hub`
- Author: `Jee-weon Jung <jungjee@users.noreply.huggingface.co>`
- Author date: `2025-02-12T21:28:36Z`
- Committer: `system <system@huggingface.co>`
- Commit date: `2025-02-12T21:28:36Z`

At the start of preparation, the tracked upstream tree was unchanged. The only untracked file was the local research document `methodology.tex`, which is not part of the ASVspoof 5 source and must never be distributed or committed.

All 18 upstream audio archives passed the MD5 checks published in the original `README.txt`. They were unpacked without changing their FLAC contents. Because this extension uses Track 1 only, the Track 2-only enrollment files (`D_A*.flac` and `E_A*.flac`) and Track 2 protocol files were omitted. The retained source contains exactly:

- 182,357 training files listed by `ASVspoof5.train.tsv`;
- 140,950 development files listed by `ASVspoof5.dev.track_1.tsv`;
- 680,774 evaluation files listed by `ASVspoof5.eval.track_1.tsv`.

The original ASVspoof 5 partitions, filenames, protocol rows, labels, and metadata are otherwise preserved unchanged.

## Local baseline commit

After preparation, the upstream Git metadata was removed and a new repository was initialized. The untouched retained Track 1 source snapshot is recorded by:

- Commit: `34c1a01b54d7b2c5407ebe839122d91dae2999ec`
- Subject: `chore: import untouched ASVspoof 5 Track 1 source files`
- Author: `Mário Veronesi Medina <mazen.mario@gmail.com>`
- Date: `2026-06-20T20:50:42-03:00`

The relocation of upstream documentation/protocols and all extension-specific files occurred after this baseline commit, so the commit can be used to inspect the retained source snapshot before project organization.
