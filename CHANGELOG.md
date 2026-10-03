# Changelog

## 1.0.0

### 2026-09-05 updates

- Preserved state and optional lineage columns in future inline Algorithm Store
  tracking adapters instead of replacing them with a six-column table.
- Derived missing mask labels from `mask.tif` and rejected invalid or duplicate
  mask-object assignments.
- Preserved each Store tracker's unmodified output in its own native CSV.
- Added validation for Store column mappings, frame numbering, lineage graphs,
  and ambiguous adapter sources.
- Added the runtime packages required by Store-generated adapters without
  replacing adapter-specific version pins.
- Preserved downloaded script contents and replaced an existing adapter only
  after syntax, dependency, and import checks succeeded.
- Required a packaged adapter entry point to be a Python file inside its ZIP
  and verified its syntax before installation.
- Excluded temporary test configuration folders from release archives.
- Added focused Store adapter tests. No bundled segmentation, tracking,
  consensus, or correction algorithm behavior changed.

### 2026-09-03 updates

- Preserved Ultrack lineage and parent relationships in the standard tracking
  CSV while retaining the native Ultrack table separately.
- Added lineage contract tests without changing consensus or correction-tool
  behavior.

### 2026-08-21 updates

- Added complete non-programmer Algorithm Store instructions to the PDF protocol,
  notebook, and README.
- Prevented Store entries from replacing included algorithms.
- Limited the remove list to methods installed from the Store and clarified that
  removal keeps the environment and downloaded files.
- Added catalog ID, HTTPS, script-checksum, archive-size, path-traversal, and
  symbolic-link validation.
- Added dedicated Algorithm Store tests and a reproducible release builder.
- No segmentation, tracking, consensus, or correction algorithm behavior changed.

### 2026-08-06 baseline

- Added a cross-platform BioTrack Studio launcher for Windows, macOS, and Linux.
- Added isolated environments for bundled segmentation, tracking, and manual
  correction tools.
- Added standardized `image.tif`, `mask.tif`, `track.csv`, and `config.yaml`
  result exchange.
- Added optional TrackPy tracking consensus assistance.
- Added optional reference-mask consensus assistance with review priorities.
- Added consensus review inside the native MMV_H4Tracks and napari-amdtrk
  correction workspaces.
- Added review classifications, on-screen review counts, and CSV exports for
  comparing flagged, confirmed, and corrected consensus sites.
- Separated tracking connection differences from objects omitted by the
  primary tracker and added site-type and review-status filters.
- Added a step-by-step user protocol for wet-laboratory researchers.
- Added a concise illustrated user protocol and a separate extended-reference
  notebook covering formats, parameters, servers, reports, and limitations.
- Added a licensed 20-frame HeLa H2B-mCherry demonstration sequence derived
  from Zenodo record 4700067 under CC BY 4.0, with complete attribution and
  processing details.
- Replaced controlled consensus illustrations with screenshots produced by the
  released models on the real demonstration sequence.
- Added a double-clickable illustrated PDF guide and a plain-text start file for
  non-coder users.
- Moved the Markdown protocol and optional notebook under `docs/` and added an
  exact demo run, correction-tool quick reference, and save verification.
- Added GitHub issue and pull-request templates and clarified third-party
  installer notices.
- Added official napari-amdtrk and MMV_H4Tracks learning links to the illustrated
  protocol while preserving BioTrack-specific opening and saving instructions.

This release does not change the embedded algorithms' scientific
logic. Consensus assistance identifies sites for human review and does not
automatically declare either result correct.
