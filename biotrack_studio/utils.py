from pathlib import Path

import numpy as np
import tifffile


def _read_single_channel(path):
    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        if any(axis in series.axes and series.shape[series.axes.index(axis)] > 1 for axis in ("C", "S")):
            raise ValueError("Input must be a single-channel 2D time series.")
        image = series.asarray()
    if image.ndim not in (2, 3):
        raise ValueError("Input must be one 2D image or one 2D time series.")
    return image


def prepare_standard_tif(input_path, output_dir):
    source = Path(input_path)
    target = Path(output_dir) / "image.tif"
    if source.is_file():
        image = _read_single_channel(source)
    else:
        files = sorted(path for path in source.iterdir() if path.is_file() and path.suffix.lower() in {".tif", ".tiff"})
        if not files:
            raise FileNotFoundError("No TIFF images found.")
        images = [_read_single_channel(path) for path in files]
        if len(images) == 1:
            image = images[0]
        else:
            if any(frame.ndim != 2 for frame in images):
                raise ValueError("Use separate 2D frame files or one multi-page TIFF; do not mix them.")
            if any(frame.shape != images[0].shape or frame.dtype != images[0].dtype for frame in images):
                raise ValueError("All input frames must have the same shape and data type.")
            image = np.stack(images)
    if image.ndim == 2:
        image = image[np.newaxis, ...]
    tifffile.imwrite(target, image, imagej=True, photometric="minisblack", metadata={"axes": "TYX"})
    return str(target)
