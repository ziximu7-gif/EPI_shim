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
echo "请完成扫描，并将全部数据复制到 ${data_dir}"
read -r -p "数据复制完成后按 Enter 开始计算..." _
for subj in "${subj_list[@]}"
do
    sub_data_dir="${data_dir}/${subj}"
    echo "Processing ${subj} in ${sub_data_dir}..."
    
    ipython "$code_path_b0map" "$sub_data_dir" "$MASTER_FILE" "$sub_name"
done
# current calculation

ipython "$code_path_coil_shim" "$data_dir" "$sub_name" "${subj_list[*]}" "$base_session"