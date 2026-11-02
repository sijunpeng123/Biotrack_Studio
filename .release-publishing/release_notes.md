# BioTrack Studio v1.0.0 — maintenance package

This package fixes installation and manual-correction compatibility and includes optional review prompts for interior track endings and starts.

- Fix StarDist model loading when Windows cannot create symbolic links.
- Use compatible StarDist framework dependencies on Windows, Linux, Apple Silicon and Intel macOS.
- Fix MMV centroid updates and support native Zarr save/read at long Windows paths.
- Resolve correction environments through Conda and preserve an existing usable PyTorch during MMV dependency repair.
- Add optional endpoint review, filtering and reporting while preserving primary results and existing disagreement IDs.

The scientific algorithms, their parameters, and the primary TIFF/CSV result format are preserved.

Download `BioTrack_Studio_v1.0.0.zip` for the complete user package, including Miniforge installers and documentation. Extract it before running the launcher. `BioTrack_Studio_v1.0.0_GitHub_Source.zip` contains the source repository, tests and CI.

Validated with real Windows launch and processing workflows, three-platform regression checks, StarDist installation and segmentation on four platform variants, and dedicated MMV native save/read tests. Validation scope is recorded in [PR #1](https://github.com/sijunpeng123/Biotrack_Studio/pull/1); checksums are supplied below and in SHA256SUMS.txt.

Reviewed source snapshot: `714897020703bd61b1ebbf76608473a227ba4dfb`.
