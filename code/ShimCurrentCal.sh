set -xeuo pipefail
# bash code/ShimCurrentCal.sh
root_dir=$PWD

sub_name=$(PYTHONPATH="$root_dir/code" python -c 'from config import paths; print(paths().subject)')
mapfile -t subj_list < <(
    PYTHONPATH="$root_dir/code" python -c 'from config import paths; print(*paths().sessions, sep="\n")'
)
data_dir="${root_dir}/data/${sub_name}"
code_path_b0map="${root_dir}/code/B0mapCal.ipynb"
code_path_coil_shim="${root_dir}/code/CoilSliceCurrentCalculation.ipynb"
base_session="${subj_list[0]}"
MASTER_FILE="${root_dir}/data/${sub_name}/${base_session}/${sub_name}_dir-AP-seepi.nii.gz"
for subj in "${subj_list[@]}"
do
    sub_data_dir="${data_dir}/${subj}"
    echo "Processing ${subj} in ${sub_data_dir}..."
    
    rm -f \
        "$sub_data_dir/AP_mean.nii.gz" \
        "$sub_data_dir/PA_mean.nii.gz" \
        "$sub_data_dir/distort_corr.nii.gz" \
        "$sub_data_dir/distort_corr_PLUS_WARP.nii.gz" \
        "$sub_data_dir/distort_corr_MINUS_WARP.nii.gz" \
        "$sub_data_dir/B0_map_Hz_Whole_FOV.nii.gz" \
        "$sub_data_dir/B0_map_Hz_to_master.nii.gz" \
        "$sub_data_dir/brain_mask.nii.gz" \
        "$sub_data_dir/B0_map_Hz_to_master_masked.nii.gz"
    ipython "$code_path_b0map" "$sub_data_dir" "$MASTER_FILE" "$sub_name"

    if [[ ! -s "$sub_data_dir/B0_map_Hz_to_master_masked.nii.gz" ]]; then
        echo "ERROR: B0 map generation failed for $subj"
        exit 1
    fi
done
# current calculation
csv_file="${data_dir}/shimming_currents.csv"
rm -f "$csv_file"
ipython "$code_path_coil_shim" "$data_dir" "$sub_name" "${subj_list[*]}" "$base_session"

if [[ ! -s "$csv_file" ]]; then
    echo "ERROR: Shim current calculation failed. CSV was not generated."
    exit 1
fi
echo "Shim calculation completed: $csv_file"