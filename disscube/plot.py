"""
Quick look at the bands of an exported GeoTIFF: one panel per band, written as a PNG.

    disscube plot cellspace.tif [-o look.png] [--bands NAME ...] [--year 2024] [--cols 2] [--shared-colorbar] [--dpi 150]

A check of the result, not a publication figure. The scale follows the values: shares in [0, 1] are
drawn as 0 to 100 %, a band of a few integer codes as discrete classes, anything else as a continuous
scale clipped at the 2nd and 98th percentiles. Cells without data are left white. ``--shared-colorbar`` draws one bar for all panels when every
panel is a share (0 to 100 %), to leave more room for the maps. Needs matplotlib
(``pip install 'disscube[plot]'``).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

#: Longest side, in pixels, of a panel: a larger raster is read decimated.
MAX_SIDE = 1600
#: A band of integer codes with at most this many distinct values is drawn as classes.
MAX_CLASSES = 12


def _kind(values: np.ndarray) -> str:
    """'share' (all in [0, 1], not integer codes), 'classes' or 'continuous'."""
    v = values[np.isfinite(values)]
    if v.size == 0:
        return "empty"
    integer = bool(np.all(v == np.round(v)))
    unique = np.unique(v)
    if integer and unique.size <= MAX_CLASSES and (v.max() > 1 or unique.size > 2):
        return "classes"
    if v.min() >= 0.0 and v.max() <= 1.0:
        return "share"
    return "continuous"


def plot_geotiff(path: str | Path, output: str | Path | None = None, *, bands: list[str] | None = None,
                 year: int | None = None, cols: int | None = None, dpi: int = 150, shared_colorbar: bool = False) -> Path:
    """Draw the bands of ``path`` (all, or those named in ``bands`` or of ``year``) into a PNG."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        raise ImportError("disscube plot needs matplotlib: pip install 'disscube[plot]'") from None
    import rasterio
    from rasterio.enums import Resampling

    path = Path(path)
    out = Path(output) if output else path.with_suffix(".png")
    with rasterio.open(path) as src:
        names = [d or f"band {i}" for i, d in enumerate(src.descriptions, 1)]
        tags = [src.tags(i) for i in range(1, src.count + 1)]
        chosen = list(range(src.count))
        if bands:
            chosen = [i for i in chosen if names[i] in bands or tags[i].get("VARIABLE") in bands]
        if year is not None:
            chosen = [i for i in chosen if tags[i].get("YEAR") == str(year)]
        if not chosen:
            raise ValueError(f"no band of {path.name} matches; bands: {', '.join(names)}")
        scale = min(1.0, MAX_SIDE / max(src.width, src.height))
        shape = (max(1, round(src.height * scale)), max(1, round(src.width * scale)))
        data = [src.read(i + 1, out_shape=shape, resampling=Resampling.nearest, masked=True)
                .astype("float64").filled(np.nan) for i in chosen]
        t = src.transform
        extent = [t.c, t.c + t.a * src.width, t.f + t.e * src.height, t.f]
        crs = src.crs.to_string() if src.crs else "unknown CRS"
        res = abs(t.a)
        unit = (src.crs.linear_units if src.crs and src.crs.is_projected else "degrees") if src.crs else "units"
        unit = {"metre": "m", "meter": "m"}.get(unit, unit)
        grid = src.tags().get("GRID_ID", "")

    ncols = cols or (1 if len(data) == 1 else 2 if len(data) <= 4 else 3)
    nrows = -(-len(data) // ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(5.2 * ncols, 4.4 * nrows), squeeze=False,
                             constrained_layout=True)
    cmaps = {"share": "viridis", "continuous": "magma"}
    shared = shared_colorbar and all(_kind(a) == "share" for a in data)
    last_im = None
    for ax, i, arr in zip(axes.flat, chosen, data, strict=False):
        kind = _kind(arr)
        if kind == "empty":
            ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
        elif kind == "classes":
            codes = np.unique(arr[np.isfinite(arr)])
            cmap = plt.get_cmap("tab20", max(len(codes), 2))
            index = np.full(arr.shape, np.nan)
            for k, c in enumerate(codes):
                index[arr == c] = k
            im = ax.imshow(index, extent=extent, cmap=cmap, vmin=-0.5, vmax=len(codes) - 0.5,
                           interpolation="nearest")
            cb = fig.colorbar(im, ax=ax, shrink=0.7, ticks=range(len(codes)))
            cb.ax.set_yticklabels([f"{c:g}" for c in codes])
            cb.set_label("class code")
        elif kind == "share":
            im = ax.imshow(arr * 100, extent=extent, cmap=cmaps[kind], vmin=0, vmax=100)
            last_im = im
            if not shared:
                fig.colorbar(im, ax=ax, shrink=0.7, label="share of the cell (%)")
        else:
            v = arr[np.isfinite(arr)]
            lo, hi = np.percentile(v, [2, 98])
            im = ax.imshow(arr, extent=extent, cmap=cmaps[kind], vmin=lo, vmax=hi if hi > lo else lo + 1)
            fig.colorbar(im, ax=ax, shrink=0.7, label="2nd to 98th percentile")
        ax.set_title(names[i], fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
    for ax in list(axes.flat)[len(data):]:
        ax.axis("off")
    if shared and last_im is not None:
        fig.colorbar(last_im, ax=list(axes.flat)[:len(data)], shrink=0.6, label="share of the cell (%)")
    fig.suptitle(f"{path.name}  {grid}  {res:g} {unit}  {crs}".replace("  ", " "), fontsize=9)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    return out
