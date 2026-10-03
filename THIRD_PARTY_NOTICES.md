# Third-Party Software

BioTrack Studio coordinates separate open-source tools in isolated conda
environments. The following projects are installed or invoked by the released
workflow:

- napari and magicgui
- Cellpose and StarDist
- TrackPy, SC-Track, and Ultrack
- MMV_H4Tracks and napari-amdtrk
- NumPy, pandas, SciPy, scikit-image, tifffile, imagecodecs, Matplotlib,
  PyTorch, TensorFlow, conda, and pip

The release archive also contains a copy of the SC-Track source distribution,
including its original `SC-Track/LICENSE` file. SC-Track is licensed under the
GNU General Public License version 3.

The complete cross-platform release archive bundles unmodified Miniforge
installers for Windows, macOS, and Linux. Miniforge installer code is licensed
under the BSD 3-Clause License. Packages installed by Miniforge have their own
license terms. See the Miniforge distribution and its embedded notices for the
applicable terms: https://github.com/conda-forge/miniforge

Cellpose, StarDist, TrackPy, Ultrack, MMV_H4Tracks, napari-amdtrk, and most
scientific Python dependencies are installed from their upstream conda or pip
distributions. They are not relicensed by BioTrack Studio.

Each third-party project remains subject to its own copyright and license terms.
Users redistributing a modified release should preserve the applicable notices
and verify the licenses of any additional algorithms they add.

Pretrained model weights and data downloaded by integrated tools remain subject
to their upstream terms. The public BioTrack Studio archive does not redistribute
Cell Tracking Challenge or Data Science Bowl microscopy images.

The included `demo_data` sequence is derived from **HeLa timelapse
(H2B-mCherry, Digital Phase Contrast)** by Romain Guiet, DOI
https://doi.org/10.5281/zenodo.4700067, under Creative Commons Attribution 4.0.
All 20 time points in the source's lighter 5-hour H2B-mCherry file were retained,
each frame was cropped to rows 320:700 and columns 280:650, and pixel intensities
were not altered. See `demo_data/DATA_SOURCE.txt` for the full attribution and
source checksum.
