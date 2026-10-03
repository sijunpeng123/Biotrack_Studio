# Acknowledgements

BioTrack Studio builds on the Python scientific-computing and bioimage-analysis ecosystem.

We acknowledge the developers and maintainers of the following projects used or integrated by this release:

- napari and magicgui for the interactive image-analysis interface.
- Cellpose and StarDist for bundled segmentation workflows.
- TrackPy, SC-Track, and Ultrack for bundled tracking workflows.
- MMV_H4Tracks and napari-amdtrk for manual correction workflows.
- NumPy, pandas, tifffile, imagecodecs, scikit-image, SciPy, PyTorch, and TensorFlow for core scientific and deep-learning infrastructure.
- conda, pip, and the open-source Python packaging ecosystem for reproducible installation.

Users should cite the original methods and software packages when using BioTrack Studio outputs in publications, reports, or derivative tools.

The included demonstration sequence is derived from Romain Guiet's **HeLa
timelapse (H2B-mCherry, Digital Phase Contrast)** dataset, DOI
https://doi.org/10.5281/zenodo.4700067, and is redistributed under CC BY 4.0.
The exact crop and source checksum are recorded in `demo_data/DATA_SOURCE.txt`.
Users remain responsible for obtaining all other datasets from official sources
and following their citation and use terms.
