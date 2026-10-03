# Contributing

Use the GitHub issue tracker to report reproducible installation or workflow
problems. Include the operating system, processor architecture, selected
segmentation and tracking methods, and the installation log. Do not upload
sensitive microscopy data.

Changes to algorithm wrappers, result schemas, or correction launchers should
include focused tests. Scientific algorithm behavior must not be changed as part
of an interface or packaging fix without separate validation and documentation.

Before submitting a change:

1. Confirm that Python files compile.
2. Run `python -m unittest discover -s tests -v`.
3. Test a standard Pipeline result folder.
4. Open both supported manual correction tools.
5. Confirm that the original result folder is unchanged by assisted correction.
6. Update `docs/USER_PROTOCOL.md` and the optional notebook when user-visible
   controls change.
7. Confirm that documentation contains no private paths or confidential images.

The PDF is generated from the Markdown protocol. Install `reportlab` and
`Pillow` in a documentation environment, then run:

```bash
python .github/scripts/build_user_guide.py --protocol docs/USER_PROTOCOL.md --output docs/BioTrack_Studio_User_Guide.pdf
```

Do not edit the PDF directly. Review the rendered cover, contents, tables, and
all screenshot pages before committing a rebuilt guide.

Pull requests must state whether scientific algorithm behavior changed. Interface,
documentation, packaging, and environment changes should remain separate from
scientific algorithm changes whenever possible.

## Algorithm Store Adapters

An Algorithm Store entry is an adapter release, not only a link to an upstream
repository. New entries must:

1. Use a lowercase ID containing only letters, numbers, dots, underscores, or
   hyphens.
2. Use an immutable HTTPS release URL and provide its SHA-256 checksum.
3. Pin Python and package versions used during validation.
4. Produce the standard `mask.tif` or `track.csv` and `config.yaml` contract.
5. Include the upstream license, required citation, supported operating systems,
   minimum BioTrack Studio version, and test-data source.
6. Pass a clean installation and a small output-contract test on Windows,
   macOS, and Linux before publication in `cloud_algos.json`.
7. Use a new adapter version for scientific or dependency changes. Do not
   silently replace an evaluated release.

Production entries should use a checksummed ZIP package. Inline `core_code` is
reserved for development testing and should not be published as a validated
method.

An inline tracking adapter must create a pandas DataFrame named
`final_track_df`. The Store wrapper accepts the standard column names or the
following default aliases: `track_id`, `original_cell_id`, `x`, and `y`. Use an
optional `column_map` object to map other source names to the standard columns,
and set `frame_base` to `1` only when source frames are one-based. The wrapper:

- preserves a supplied `state` column and fills only missing values with
  `none`;
- obtains `continuous_label` from `mask.tif` when the adapter does not supply
  it;
- preserves `lineageId` and `parentTrackId` only when both are supplied and
  their parent graph is valid; and
- writes the complete unmodified table to
  `tracking_output/<algorithm_id>_native_track.csv` before normalization.

Lineage-aware inline adapters must use positive `trackId` and `lineageId`
values. Use `parentTrackId = 0` for a root track. Parent and child tracks must
share one `lineageId`. The wrapper rejects missing mask labels, duplicate
frame/object assignments, missing parents, self-parent relations, and lineage
cycles instead of silently changing them. Package and script adapters are
responsible for applying the same result contract in their own entry script.
