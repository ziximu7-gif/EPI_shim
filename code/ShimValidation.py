"""Compare measured pre-shim and post-shim EPI-derived B0 maps.

Run after B0mapCal.ipynb has completed for both sessions.
All statistics use ONE shared finite ROI (intersection of both session masks),
and all three images are defined on the same master voxel grid.
No DAC or scanner communication takes place in this script.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import nibabel as nib
import numpy as np

MAP_NAME = "B0_map_Hz_to_master.nii.gz"
MASK_NAME = "brain_mask_to_master.nii.gz"


def get_volume(path: Path):
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f"Missing or empty NIfTI: {path}")
    image = nib.load(str(path))
    volume = np.asarray(image.get_fdata(dtype=np.float32))
    if volume.ndim != 3:
        raise ValueError(f"Expected a 3D volume, got {volume.shape}: {path}")
    return volume, image.affine


def require_same_grid(shape_a, affine_a, shape_b, affine_b, label):
    if shape_a != shape_b or not np.allclose(affine_a, affine_b, atol=1e-3, rtol=0):
        raise ValueError(f"{label} is not on the baseline master grid; refusing voxelwise comparison")


def read_total_readout(session_dir: Path, subject: str):
    path = session_dir / f"{subject}_dir-AP-seepi.json"
    with path.open(encoding="utf-8") as handle:
        meta = json.load(handle)
    trt = float(meta["TotalReadoutTime"])
    if not np.isfinite(trt) or trt <= 0:
        raise ValueError(f"Invalid TotalReadoutTime in {path}: {trt}")
    return trt


def statistics(arr):
    return {
        "mean_hz": float(np.mean(arr, dtype=np.float64)),
        "std_hz": float(np.std(arr, dtype=np.float64, ddof=0)),
        "peak_to_peak_hz": float(np.ptp(arr)),
        "p95_minus_p05_hz": float(np.percentile(arr, 95) - np.percentile(arr, 5)),
    }


def improvement(before, after):
    return None if before <= 0 else 100.0 * (1.0 - after / before)


def draw_maps(before, after, mask, stats_before, stats_after, output_path):
    """Representative axial slice; both states use a shared Hz scale."""
    bmean, amean = stats_before["mean_hz"], stats_after["mean_hz"]
    b = before - bmean
    a = after - amean
    difference = a - b  # Global offset removed from both states.

    z = int(np.argmax(mask.sum(axis=(0, 1))))
    valid_b = b[mask]
    valid_a = a[mask]
    shared_limit = float(np.percentile(np.abs(np.concatenate((valid_b, valid_a))), 99))
    shared_limit = max(shared_limit, 1e-6)
    diff_limit = float(np.percentile(np.abs(difference[mask]), 99))
    diff_limit = max(diff_limit, 1e-6)

    images = [
        np.where(mask[:, :, z], b[:, :, z], np.nan).T,
        np.where(mask[:, :, z], a[:, :, z], np.nan).T,
        np.where(mask[:, :, z], difference[:, :, z], np.nan).T,
    ]

    fig, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
    im0 = axes[0].imshow(images[0], cmap="coolwarm", origin="lower", vmin=-shared_limit, vmax=shared_limit)
    axes[1].imshow(images[1], cmap="coolwarm", origin="lower", vmin=-shared_limit, vmax=shared_limit)
    im2 = axes[2].imshow(images[2], cmap="PuOr_r", origin="lower", vmin=-diff_limit, vmax=diff_limit)

    axes[0].set_title(f"Before  |  SD {stats_before['std_hz']:.3f} Hz")
    axes[1].set_title(f"After  |  SD {stats_after['std_hz']:.3f} Hz")
    axes[2].set_title("After - before (centered)")
    for ax in axes:
        ax.set_xlabel("Voxel X")
        ax.set_ylabel("Voxel Y")
    fig.colorbar(im0, ax=[axes[0], axes[1]], shrink=0.78, label="B0 relative to ROI mean (Hz)")
    fig.colorbar(im2, ax=axes[2], shrink=0.78, label="B0 difference (Hz)")
    fig.suptitle(f"Pre/post shim B0 comparison | axial voxel index z={z} | shared ROI")
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def draw_histogram(before_values, after_values, output_path):
    b = before_values - np.mean(before_values)
    a = after_values - np.mean(after_values)
    both = np.concatenate((b, a))
    lo, hi = np.percentile(both, [1, 99])
    if hi - lo < 1e-6:
        lo, hi = -0.5, 0.5
    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
    ax.hist(b, bins=100, range=(lo, hi), density=True, alpha=0.5, label="Before")
    ax.hist(a, bins=100, range=(lo, hi), density=True, alpha=0.5, label="After")
    ax.set_xlabel("B0 relative to ROI mean (Hz)")
    ax.set_ylabel("Density")
    ax.set_title("B0 distributions in the SAME ROI (display range P1-P99)")
    ax.legend()
    ax.grid(alpha=0.25)
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def validate(baseline: Path, post_shim: Path, output: Path, subject: str):
    # The conversion to Hz requires matching, validated EPI acquisition settings.
    trt_before = read_total_readout(baseline, subject)
    trt_after = read_total_readout(post_shim, subject)
    if not np.isclose(trt_before, trt_after, rtol=1e-6, atol=1e-9):
        raise ValueError(
            f"AP TotalReadoutTime changed: before={trt_before} s, after={trt_after} s. "
            "Verify that both EPI protocols match."
        )

    before, affine = get_volume(baseline / MAP_NAME)
    after, affine_after = get_volume(post_shim / MAP_NAME)
    require_same_grid(before.shape, affine, after.shape, affine_after, "Post-shim B0 map")

    mask_before, maffine_before = get_volume(baseline / MASK_NAME)
    mask_after, maffine_after = get_volume(post_shim / MASK_NAME)
    require_same_grid(before.shape, affine, mask_before.shape, maffine_before, "Baseline ROI mask")
    require_same_grid(before.shape, affine, mask_after.shape, maffine_after, "Post-shim ROI mask")

    valid_before = (mask_before > 0.5) & np.isfinite(before)
    valid_after = (mask_after > 0.5) & np.isfinite(after)
    shared = valid_before & valid_after
    n_shared = int(np.count_nonzero(shared))
    if n_shared < 100:
        raise ValueError(f"Only {n_shared} shared valid voxels; cannot compare B0 maps reliably")
    overlap_fraction = n_shared / max(1, int(np.count_nonzero(valid_before | valid_after)))

    values_before = before[shared]
    values_after = after[shared]
    sb, sa = statistics(values_before), statistics(values_after)
    std_improvement = improvement(sb["std_hz"], sa["std_hz"])
    ptp_improvement = improvement(sb["peak_to_peak_hz"], sa["peak_to_peak_hz"])

    output.mkdir(parents=True, exist_ok=True)
    map_png = output / "B0_before_after_comparison.png"
    histogram_png = output / "B0_distribution_comparison.png"
    draw_maps(before, after, shared, sb, sa, map_png)
    draw_histogram(values_before, values_after, histogram_png)

    rows = [
        ("Mean", "mean_hz", None),
        ("Standard deviation", "std_hz", std_improvement),
        ("Peak-to-peak", "peak_to_peak_hz", ptp_improvement),
        ("P95-P05", "p95_minus_p05_hz", improvement(sb["p95_minus_p05_hz"], sa["p95_minus_p05_hz"])),
    ]
    with (output / "B0_comparison_metrics.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "before_Hz", "after_Hz", "after_minus_before_Hz", "improvement_percent"])
        for name, key, imp in rows:
            writer.writerow([name, f"{sb[key]:.6f}", f"{sa[key]:.6f}", f"{sa[key] - sb[key]:.6f}", "" if imp is None else f"{imp:.4f}"])

    lines = [
        "Post-shim B0 validation (measured EPI-derived B0 maps)",
        f"Baseline: {baseline}",
        f"Post-shim: {post_shim}",
        f"TotalReadoutTime: {trt_before:g} s (both AP protocols)",
        f"Shared ROI voxels: {n_shared:,}",
        f"ROI intersection / union: {overlap_fraction:.1%}",
        "ROI: intersection of baseline and post-shim resampled masks; finite B0 in both.",
        "Both states are compared on the SAME ROI and master affine.",
        "Figures subtract each volume's shared-ROI mean to emphasize spatial inhomogeneity.",
        "Figure color limits are set at 99th percentile absolute magnitude for visualization.",
        "Peak-to-peak is max-min and can be affected strongly by individual outlier voxels.",
        "",
        f"Before B0 SD: {sb['std_hz']:.4f} Hz",
        f"After  B0 SD: {sa['std_hz']:.4f} Hz",
        f"SD improvement: {'N/A' if std_improvement is None else f'{std_improvement:.2f}%'}",
        f"Before B0 peak-to-peak: {sb['peak_to_peak_hz']:.4f} Hz",
        f"After  B0 peak-to-peak: {sa['peak_to_peak_hz']:.4f} Hz",
        f"Peak-to-peak improvement: {'N/A' if ptp_improvement is None else f'{ptp_improvement:.2f}%'}",
        f"Before P95-P05: {sb['p95_minus_p05_hz']:.4f} Hz",
        f"After  P95-P05: {sa['p95_minus_p05_hz']:.4f} Hz",
    ]
    if overlap_fraction < 0.8:
        lines.extend(["", "WARNING: Shared ROI overlap below 80%; inspect registration/masks before interpreting results."])
    (output / "B0_comparison_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nGenerated files in {output}:")
    for name in [map_png.name, histogram_png.name, "B0_comparison_metrics.csv", "B0_comparison_summary.txt"]:
        print(f"  {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--post-shim", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--subject", type=str, required=True)
    args = parser.parse_args()
    validate(args.baseline, args.post_shim, args.output, args.subject)


if __name__ == "__main__":
    main()
