import os, tifffile, numpy as np, glob
def prepare_standard_tif(input_path, output_dir):
    target = os.path.join(output_dir, "image.tif")
    if os.path.isfile(input_path):
        img = tifffile.imread(input_path)
    else:
        files = sorted(glob.glob(os.path.join(input_path, "*.tif*")))
        if not files: raise FileNotFoundError("No TIFs found.")
        sample = tifffile.imread(files[0])
        img = np.zeros((len(files), *sample.shape), dtype=sample.dtype)
        for i, f in enumerate(files): img[i] = tifffile.imread(f)
    tifffile.imwrite(target, img, imagej=True)
    return target
