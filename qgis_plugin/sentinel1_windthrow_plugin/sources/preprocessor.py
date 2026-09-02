"""SAR preprocessor for Sentinel-1 GRD scenes.

Provides functions to convert amplitude to decibels, apply speckle filtering,
and create a land/water mask. All operations work on NumPy arrays and can
be applied to GeoTIFF files using GDAL (always available inside QGIS).
"""

from __future__ import annotations

import os
from typing import Tuple, Optional

import numpy as np

try:
    from osgeo import gdal  # always present in QGIS Python env
    gdal.UseExceptions()
except Exception:  # pragma: no cover
    gdal = None  # type: ignore

try:
    import scipy.ndimage
except Exception:  # pragma: no cover
    scipy = None  # type: ignore


#: Fraction of negative pixels that marks an image as "already in dB".
_DB_DETECT_FRACTION = 0.05
#: Values below this are treated as no-data sentinels, not physical dB —
#: even the noisiest ocean sit well above -60 dB, while common SAR
#: no-data values (-32768, -9999) live far below this bound.
_DB_NODATA_FLOOR = -100.0


class SARPreprocessor:
    """Preprocess SAR amplitude GeoTIFFs.

    The class does not hold state; each method processes an input array
    and returns the processed array. For file-based operations, see the
    static helper methods.
    """

    @staticmethod
    def to_db(amplitude: np.ndarray) -> np.ndarray:
        """Convert amplitude (linear) to decibels.

        Parameters
        ----------
        amplitude: np.ndarray
            Input amplitude. Zero and negative values are treated as
            no-data and replaced with NaN (not -inf). Downstream code
            should use ``np.nanpercentile`` / ``np.nanmin`` etc. to
            skip NaN. This prevents zero-amplitude border pixels from
            becoming -379 dB outliers that skew percentile stretches
            and make the entire image look white.

        Returns
        -------
        np.ndarray
            Decibel values (10 * log10(amplitude)), float32. NaN where
            input was <= 0.
        """
        amp = amplitude.astype(np.float32, copy=True)
        # Mark zero / negative as NaN — these are no-data or noise floor.
        # Using NaN (not tiny) prevents extreme dB outliers that would
        # otherwise dominate the percentile stretch.
        amp[amp <= 0] = np.nan
        return 10.0 * np.log10(amp)

    @staticmethod
    def from_db(db: np.ndarray) -> np.ndarray:
        """Convert decibels back to amplitude."""
        return np.power(10.0, db / 10.0).astype(np.float32)

    @staticmethod
    def is_db_domain(amplitude: np.ndarray) -> bool:
        """Detect whether ``amplitude`` is already in the decibel domain.

        Sentinel-1 data reaches the analysis/preprocess steps in one of
        three scales — calibrated dB (sea pixels negative), linear
        amplitude/power (values >= 0) or raw uncalibrated GRD digital
        numbers (positive integers). A bare ``min < 0`` probe conflates
        raw-DN no-data sentinels (``-32768``) with physical negative dB
        values, so we count only *negative-but-above-the-no-data-floor*
        pixels. More than ``_DB_DETECT_FRACTION`` of them => already dB.
        """
        arr = amplitude.astype(np.float32, copy=False)
        finite = np.isfinite(arr)
        n_finite = int(np.count_nonzero(finite))
        if n_finite == 0:
            return False
        physical_negative = (arr < 0.0) & (arr > _DB_NODATA_FLOOR)
        neg_frac = float(np.count_nonzero(physical_negative)) / float(n_finite)
        return neg_frac > _DB_DETECT_FRACTION

    @staticmethod
    def to_db_domain(amplitude: np.ndarray) -> np.ndarray:
        """Return ``amplitude`` converted to the decibel domain.

        Auto-detects the input scale via :meth:`is_db_domain`: already-dB
        images are returned unchanged (extreme no-data sentinels masked to
        NaN); linear amplitude / raw DN is converted with :meth:`to_db`,
        which also NaN-maps ``<= 0`` border pixels.
        """
        arr = amplitude.astype(np.float32, copy=True)
        finite = np.isfinite(arr)
        n_finite = int(np.count_nonzero(finite))
        if n_finite == 0:
            return arr
        physical_negative = (arr < 0.0) & (arr > _DB_NODATA_FLOOR)
        neg_frac = float(np.count_nonzero(physical_negative)) / float(n_finite)
        if neg_frac > _DB_DETECT_FRACTION:
            # Already dB — mask no-data sentinels, keep physical values.
            arr[~(finite & (arr > _DB_NODATA_FLOOR))] = np.nan
            return arr
        return SARPreprocessor.to_db(arr)

    @staticmethod
    def speckle_filter(
        image: np.ndarray,
        kernel_size: int = 5,
        method: str = "lee",
    ) -> np.ndarray:
        """Apply a speckle reduction filter.

        Parameters
        ----------
        image: np.ndarray
            Input image (amplitude or dB). Will be treated as 2D; if 3D,
            the first dimension is assumed to be bands and each band is
            processed independently.
        kernel_size: int
            Size of the square kernel (must be odd and >= 3).
        method: str
            Only "lee" is implemented for MVP; other methods can be added.

        Returns
        -------
        np.ndarray
            Filtered image of same shape and dtype as input.
        """
        if scipy is None:  # pragma: no cover
            raise ImportError("scipy is required for speckle filtering")

        if kernel_size % 2 == 0 or kernel_size < 3:
            raise ValueError("kernel_size must be odd and >= 3")

        # Work on a copy to avoid modifying original
        img = image.astype(np.float32, copy=True)

        # If multi-band, process each band
        if img.ndim == 3:
            bands = []
            for b in range(img.shape[0]):
                bands.append(
                    SARPreprocessor._speckle_filter_band(
                        img[b], kernel_size=kernel_size, method=method
                    )
                )
            return np.stack(bands, axis=0)
        else:
            return SARPreprocessor._speckle_filter_band(
                img, kernel_size=kernel_size, method=method
            )

    @staticmethod
    def _speckle_filter_band(
        band: np.ndarray, kernel_size: int, method: str
    ) -> np.ndarray:
        """Lee filter for a single band.

        Handles NaN values (produced by ``to_db`` for zero/negative
        amplitude): NaN pixels are treated as missing data. The local
        convolution fills them in from neighbors using the "reflect"
        boundary mode, and the global statistics use ``nanmean`` /
        ``nanvar`` to skip NaN.
        """
        if method != "lee":
            raise NotImplementedError(f"Speckle filter method '{method}' not implemented")

        # Lee filter implementation using local statistics
        kernel = np.ones((kernel_size, kernel_size), dtype=np.float32)
        kernel_size_float = float(kernel_size * kernel_size)

        # Build a mask of valid (non-NaN) pixels.
        valid_mask = np.isfinite(band)
        if not valid_mask.any():
            # Entire block is NaN — nothing to filter.
            return band.copy()

        # Replace NaN with 0 for convolution (so they don't pollute the
        # sum), but track the valid-pixel count to correct the mean.
        band_filled = np.where(valid_mask, band, 0.0)
        valid_count = scipy.ndimage.convolve(
            valid_mask.astype(np.float32), kernel, mode="reflect"
        )

        # Local sum and sum-of-squares (using NaN-filled version).
        local_sum = scipy.ndimage.convolve(band_filled, kernel, mode="reflect")
        local_sumsq = scipy.ndimage.convolve(
            band_filled * band_filled, kernel, mode="reflect"
        )

        # Local mean = sum / count (avoid division by zero).
        safe_count = np.maximum(valid_count, 1.0)
        mean = local_sum / safe_count
        second = local_sumsq / safe_count
        var = second - mean * mean
        var = np.maximum(var, 0.0)

        # Overall mean and variance (skip NaN).
        overall_mean = float(np.nanmean(band))
        overall_var = float(np.nanvar(band))

        # Weight function: weight = local_var / (local_var + noise_var)
        weight = var / (var + overall_var + 1e-10)

        # Filtered image = weight * mean + (1 - weight) * image.
        # For pixels that were NaN, use the local mean (which is derived
        # from neighbors); for valid pixels, blend original and mean.
        filtered = np.where(
            valid_mask,
            weight * mean + (1.0 - weight) * band,
            mean,
        )
        return filtered.astype(np.float32, copy=False)

    @staticmethod
    def land_mask(
        amplitude: np.ndarray,
        threshold_db: float = -20.0,
        polarizations: Optional[Tuple[str, ...]] = None,
        is_db: bool = False,
    ) -> np.ndarray:
        """Create a binary land/water mask from SAR backscatter.

        Pixels with backscatter below ``threshold_db`` (in dB) are
        considered water (True); the rest are land (False).

        Parameters
        ----------
        amplitude: np.ndarray
            Input array. Either 2D (H, W) for a single band or 3D
            (bands, H, W) for multi-band data. Linear amplitude by
            default; pass ``is_db=True`` if the values are already in
            decibels and no conversion should be applied.
        threshold_db: float
            Threshold in decibels. Default -20 dB.
        polarizations: tuple of str or None
            Optional polarisation labels for multi-band input. If given,
            the mask is combined across bands with OR (any band below
            threshold => water). If None, multi-band input is still
            combined with OR.
        is_db: bool
            If True, the input is already in dB and will not be
            converted. If False (default), input is assumed to be linear
            amplitude and is converted to dB before thresholding.

        Returns
        -------
        np.ndarray
            Boolean 2D array where True = water, False = land.
        """
        arr = amplitude.astype(np.float32, copy=False)

        # Convert to dB if the input is linear amplitude.
        if not is_db:
            if arr.ndim == 2:
                db = SARPreprocessor.to_db(arr)
            elif arr.ndim == 3:
                db = np.stack(
                    [SARPreprocessor.to_db(arr[i]) for i in range(arr.shape[0])],
                    axis=0,
                )
            else:
                raise ValueError(f"Unsupported array shape: {arr.shape}")
        else:
            db = arr

        # Water mask per band: below threshold => water.
        water_mask = db < threshold_db
        if water_mask.ndim == 3:
            # Combine bands: water if any band indicates water.
            water_mask = np.any(water_mask, axis=0)
        return water_mask

    # ------------------------------------------------------------------
    # File-based helpers (optional, for use in QgsTask)
    # ------------------------------------------------------------------
    @staticmethod
    def process_file(
        input_path: str,
        output_path: str,
        to_db: bool = False,
        speckle: bool = False,
        speckle_kernel: int = 5,
        land_mask: bool = False,
        land_mask_threshold_db: float = -20.0,
        output_dtype: str = "float32",
    ) -> None:
        """Process a single GeoTIFF file and write the result using GDAL.

        Parameters
        ----------
        input_path, output_path: str
            File paths.
        to_db: bool
            Convert amplitude to decibels.
        speckle: bool
            Apply speckle filter (Lee).
        speckle_kernel: int
            Kernel size for speckle filter.
        land_mask: bool
            If True, output a byte mask (0=land, 255=water) instead of
            processing the image; overrides to_db and speckle.
        land_mask_threshold_db: float
            Threshold for land/water mask (only used if land_mask=True).
        output_dtype: str
            dtype for output array (only 'float32'/'float64'/'uint8' honored).
        """
        if gdal is None:  # pragma: no cover
            raise ImportError("osgeo.gdal is required for file processing")

        if not os.path.isfile(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")

        # ---- Open input ----------------------------------------------------
        src = gdal.Open(input_path, gdal.GA_ReadOnly)
        if src is None:
            raise RuntimeError(
                f"GDAL cannot open '{input_path}'. "
                f"File may be corrupt or not a supported raster format."
            )

        try:
            band_count = src.RasterCount
            xsize = src.RasterXSize
            ysize = src.RasterYSize
            if xsize == 0 or ysize == 0 or band_count == 0:
                raise RuntimeError(
                    f"Input raster has invalid dimensions: "
                    f"{band_count} bands, {xsize}x{ysize}"
                )

            # ---- Collect ALL georeference information from the source ----
            # Sentinel-1 GRD GeoTIFFs typically carry:
            #   * an identity GeoTransform (pixel/line = 1.0, no rotation)
            #   * NO meaningful projection on the GeoTransform
            #   * a set of Ground Control Points (GCPs) that describe the
            #     radar-to-geographic mapping as a grid
            #   * a GCP projection (usually EPSG:4326 / WGS84)
            # If we copy only GeoTransform + Projection (as the previous
            # version did), the output TIFF ends up with identity transform
            # and no GCPs -> QGIS puts it at (0, 0). We must copy GCPs
            # explicitly.
            geotransform = src.GetGeoTransform()
            projection = src.GetProjection()
            gcps = src.GetGCPs()
            gcp_projection = src.GetGCPProjection()

            # Per-band auxiliary info (nodata, scale, offset, unit).
            # Preserved so that downstream consumers (QGIS layer properties,
            # raster calculators) see the same value range as the input.
            band_infos = []
            for i in range(band_count):
                b = src.GetRasterBand(i + 1)
                band_infos.append({
                    "nodata": b.GetNoDataValue(),
                    "scale": b.GetScale(),
                    "offset": b.GetOffset(),
                    "unit": b.GetUnitType(),
                    "description": b.GetDescription(),
                    "color_interp": b.GetColorInterpretation(),
                })

            # Dataset-level metadata (Sentinel-1 TIFFs carry TIMELINE,
            # PRODUCT_TYPE, MODE, POLARISATION, etc. here).
            dataset_metadata = {}
            for domain in (None, "IMAGE_STRUCTURE", "DERIVED_SUBDATASETS"):
                try:
                    md = src.GetMetadata_Dict(domain) if domain else src.GetMetadata_Dict()
                except Exception:
                    md = {}
                if md:
                    dataset_metadata[domain] = dict(md)

            # Probe band 1 to detect whether input is already in dB.
            # We only need a small sample (first 256 rows) — cheap.
            probe_rows = min(256, ysize)
            probe = src.GetRasterBand(1).ReadAsArray(
                xoff=0, yoff=0, win_xsize=xsize, win_ysize=probe_rows
            )
            if probe is None:
                raise RuntimeError(f"Failed to probe band 1 of '{input_path}'")
            # Nodata-aware dB detection: a raw-DN GRD band carries -32768
            # no-data pixels that a bare `min < 0` would misread as dB.
            already_db = SARPreprocessor.is_db_domain(probe.astype(np.float32))
            del probe  # free memory before main loop
        finally:
            # Keep `src` open for the block-wise reads below — close
            # explicitly after all blocks are processed.
            pass

        # ---- Decide output dtype / band count -----------------------------
        if land_mask:
            out_gdal_dtype = gdal.GDT_Byte
            out_band_count = 1
        else:
            if output_dtype == "float64":
                out_gdal_dtype = gdal.GDT_Float64
            else:
                out_gdal_dtype = gdal.GDT_Float32
            out_band_count = band_count

        # ---- Open output dataset (empty, will be filled block by block) ---
        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        driver = gdal.GetDriverByName("GTiff")
        if driver is None:
            raise RuntimeError("GDAL GTiff driver not available")

        if os.path.exists(output_path):
            try:
                gdal.GetDriverByName("GTiff").Delete(output_path)
            except Exception:
                try:
                    os.remove(output_path)
                except OSError:
                    pass

        dst = driver.Create(
            output_path,
            xsize,
            ysize,
            out_band_count,
            out_gdal_dtype,
            options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"],
        )
        if dst is None:
            try:
                src = None
            except Exception:
                pass
            raise RuntimeError(f"Cannot create output file: {output_path}")

        try:
            # ---- Restore ALL georeference information ----
            had_gcps = bool(gcps) and bool(gcp_projection)
            if had_gcps:
                dst.SetGCPs(gcps, gcp_projection)
            else:
                if geotransform and tuple(geotransform) != (0.0, 1.0, 0.0, 0.0, 0.0, 1.0):
                    dst.SetGeoTransform(geotransform)
                elif geotransform:
                    dst.SetGeoTransform(geotransform)
                if projection:
                    dst.SetProjection(projection)

            # Copy dataset-level metadata.
            for domain, md in dataset_metadata.items():
                try:
                    if domain is None:
                        dst.SetMetadata(md)
                    else:
                        dst.SetMetadata(md, domain)
                except Exception:
                    pass

            # Copy per-band metadata (description, unit, nodata, etc.).
            # For land-mask branch we skip — byte mask has different value range.
            copy_band_info = not land_mask
            if copy_band_info:
                for i in range(min(out_band_count, len(band_infos))):
                    info = band_infos[i]
                    band = dst.GetRasterBand(i + 1)
                    try:
                        if info["nodata"] is not None:
                            band.SetNoDataValue(float(info["nodata"]))
                    except Exception:
                        pass
                    try:
                        if info["scale"] is not None and info["scale"] != 1.0:
                            band.SetScale(float(info["scale"]))
                    except Exception:
                        pass
                    try:
                        if info["offset"] is not None and info["offset"] != 0.0:
                            band.SetOffset(float(info["offset"]))
                    except Exception:
                        pass
                    try:
                        if info["unit"]:
                            band.SetUnitType(info["unit"])
                    except Exception:
                        pass
                    try:
                        if info["description"]:
                            band.SetDescription(info["description"])
                    except Exception:
                        pass

            # ---- Block-wise processing -----------------------------------
            # Use the source's native block size if possible (tiled TIFFs
            # are typically 256x256; striped TIFFs use full-width strips).
            # We process one block-row at a time (yoff, ysize_block).
            # For speckle filtering we need `kernel_size//2` rows of
            # overlap above and below the target block.
            overlap = (speckle_kernel // 2) if (speckle and not land_mask) else 0
            # Target block size in rows. Smaller blocks = less peak memory,
            # but more iterations. 1024 rows is a good default for 25k-wide
            # Sentinel-1 GRD (~100 MB per band at float32).
            target_block_rows = 1024

            try:
                # Stats accumulator for the output band(s). We compute
                # streaming min/max/sum/sum_sq over all blocks so we can
                # call SetStatistics at the end without GDAL re-reading
                # the file.
                stats = [{"min": None, "max": None,
                          "sum": 0.0, "sumsq": 0.0, "count": 0}
                         for _ in range(out_band_count)]

                yoff = 0
                while yoff < ysize:
                    # Compute block boundaries with overlap.
                    y_block_start = max(0, yoff - overlap)
                    y_block_end = min(ysize, yoff + target_block_rows + overlap)
                    # Slice that we will WRITE to the output (no overlap).
                    y_write_start = yoff
                    y_write_end = min(ysize, yoff + target_block_rows)
                    y_write_h = y_write_end - y_write_start
                    # Read height includes overlap on both sides.
                    y_read_h = y_block_end - y_block_start
                    # Offset of the write region inside the read region.
                    y_inner_off = yoff - y_block_start

                    # Read all bands for this y-range.
                    # Shape: (band_count, y_read_h, xsize) float32.
                    block = np.zeros(
                        (band_count, y_read_h, xsize), dtype=np.float32
                    )
                    for b_idx in range(band_count):
                        arr = src.GetRasterBand(b_idx + 1).ReadAsArray(
                            xoff=0, yoff=y_block_start,
                            win_xsize=xsize, win_ysize=y_read_h,
                        )
                        if arr is None:
                            raise RuntimeError(
                                f"Failed to read block yoff={y_block_start} "
                                f"h={y_read_h} from band {b_idx+1} of "
                                f"'{input_path}'"
                            )
                        block[b_idx] = arr.astype(np.float32, copy=False)

                    # ---- Process the block ----
                    if land_mask:
                        mask = SARPreprocessor.land_mask(
                            block, threshold_db=land_mask_threshold_db,
                            is_db=already_db,
                        )
                        # mask shape: (y_read_h, xsize) bool
                        # Slice off the overlap before writing.
                        out_block = (
                            mask[y_inner_off:y_inner_off + y_write_h]
                            .astype(np.uint8) * 255
                        )
                        out_block = out_block[np.newaxis, ...]
                    else:
                        out_block = block
                        if to_db and not already_db:
                            out_block = SARPreprocessor.to_db(out_block)
                        if speckle:
                            out_block = SARPreprocessor.speckle_filter(
                                out_block,
                                kernel_size=speckle_kernel,
                                method="lee",
                            )
                        # Slice off the overlap.
                        out_block = out_block[:, y_inner_off:y_inner_off + y_write_h, :]
                        # Cast to target dtype.
                        if output_dtype == "float64":
                            out_block = out_block.astype(np.float64)
                        else:
                            out_block = out_block.astype(np.float32)

                    # ---- Write block to output ----
                    for b_idx in range(out_band_count):
                        band = dst.GetRasterBand(b_idx + 1)
                        band.WriteArray(
                            out_block[b_idx],
                            xoff=0, yoff=y_write_start,
                        )

                        # Streaming stats (ignore NaN).
                        try:
                            arr_flat = out_block[b_idx].ravel()
                            valid = arr_flat[np.isfinite(arr_flat)]
                            if valid.size > 0:
                                s = stats[b_idx]
                                bmin = float(np.min(valid))
                                bmax = float(np.max(valid))
                                s["min"] = bmin if s["min"] is None else min(s["min"], bmin)
                                s["max"] = bmax if s["max"] is None else max(s["max"], bmax)
                                s["sum"] += float(np.sum(valid))
                                s["sumsq"] += float(np.sum(valid * valid))
                                s["count"] += int(valid.size)
                        except Exception:
                            pass

                    # Free memory before next iteration.
                    del block, out_block

                    yoff += target_block_rows

                # Flush all bands.
                for b_idx in range(out_band_count):
                    band = dst.GetRasterBand(b_idx + 1)
                    band.FlushCache()
                    # Set statistics from our streaming accumulator.
                    # Much faster than GDAL's ComputeStatistics (which
                    # re-reads the entire file).
                    s = stats[b_idx]
                    if s["min"] is not None and s["count"] > 0:
                        mean = s["sum"] / s["count"]
                        var = max(0.0, s["sumsq"] / s["count"] - mean * mean)
                        stddev = float(np.sqrt(var))
                        try:
                            band.SetStatistics(
                                float(s["min"]), float(s["max"]),
                                float(mean), float(stddev)
                            )
                        except Exception:
                            pass
            finally:
                # Make sure source dataset is released.
                try:
                    src = None
                except Exception:
                    pass
        finally:
            dst = None  # release and finalize output file

