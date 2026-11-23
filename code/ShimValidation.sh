#!/usr/bin/env bash
# Runs from the repository root via: uv run bash code/ShimValidation.sh [ses-11]
set -euo pipefail

root_dir="$PWD"
validation_session="${1:-ses-11}"
if [[ ! "$validation_session" =~ ^ses-[A-Za-z0-9]+$ ]]; then
    echo "ERROR: Invalid validation session name: $validation_session" >&2
    exit 2
fi

sub_name=$(PYTHONPATH="$root_dir/code" python -c 'from config import paths; print(paths().subject)')
base_session=$(PYTHONPATH="$root_dir/code" python -c 'from config import paths; print(paths().base_session)')
mapfile -t calibration_sessions < <(
    PYTHONPATH="$root_dir/code" python -c 'from config import paths; print(*paths().sessions, sep="\n")'
)
for s in "${calibration_sessions[@]}"; do
    if [[ "$s" == "$validation_session" ]]; then
        echo "ERROR: $validation_session is a calibration session; choose another name" >&2
        exit 2
    fi
done

subject_dir="$root_dir/data/$sub_name"
validation_dir="$subject_dir/$validation_session"
base_dir="$subject_dir/$base_session"
master_file="$base_dir/${sub_name}_dir-AP-seepi.nii.gz"

# Check BEFORE removing any derived outputs. Never delete MRI raw data.
for file in \
    "$master_file" \
    "$validation_dir/${sub_name}_dir-AP-seepi.nii.gz" \
    "$validation_dir/${sub_name}_dir-PA-seepi.nii.gz" \
    "$validation_dir/${sub_name}_dir-AP-seepi.json" \
    "$base_dir/B0_map_Hz_to_master.nii.gz" \
    "$base_dir/brain_mask_to_master.nii.gz"; do
    if [[ ! -s "$file" ]]; then
        echo "ERROR: Missing or empty file: $file" >&2
        exit 1
    fi
done

# Clear derived outputs in validation session only. Mirrors ShimCurrentCal.sh.
rm -f \
    "$validation_dir/AP_mean.nii.gz" \
    "$validation_dir/PA_mean.nii.gz" \
    "$validation_dir/distort_corr.nii.gz" \
    "$validation_dir/distort_corr_PLUS_WARP.nii.gz" \
    "$validation_dir/distort_corr_MINUS_WARP.nii.gz" \
    "$validation_dir/B0_map_Hz_Whole_FOV.nii.gz" \
    "$validation_dir/B0_map_Hz_to_master.nii.gz" \
    "$validation_dir/brain_mask.nii.gz" \
    "$validation_dir/brain_mask_to_master.nii.gz" \
    "$validation_dir/B0_map_Hz_to_master_masked.nii.gz"

echo "Processing post-shim B0 map from $validation_session ..."
ipython "$root_dir/code/B0mapCal.ipynb" "$validation_dir" "$master_file" "$sub_name"

for file in \
    "$validation_dir/B0_map_Hz_to_master.nii.gz" \
    "$validation_dir/brain_mask_to_master.nii.gz"; do
    if [[ ! -s "$file" ]]; then
        echo "ERROR: Post-shim map generation did not produce $file" >&2
        exit 1
    fi
done

output_dir="$subject_dir/shim_validation/$validation_session"
python "$root_dir/code/ShimValidation.py" \
    --baseline "$base_dir" \
    --post-shim "$validation_dir" \
    --output "$output_dir" \
    --subject "$sub_name"

echo "Shim validation complete: $output_dir"
