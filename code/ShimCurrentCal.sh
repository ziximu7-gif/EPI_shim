set -xeuo pipefail
# bash code/ShimCurrentCal.sh
sub_name="sub-03"
subj_list=(ses-01 ses-02  ses-03  ses-04  ses-05  ses-06  ses-07  ses-08  ses-10)
root_dir=$PWD
data_dir="${root_dir}/data/${sub_name}"
code_path_b0map="${root_dir}/code/B0mapCal.ipynb"
code_path_coil_shim="${root_dir}/code/CoilSliceCurrentCalculation.ipynb"
# b0 map编号，同时作为padding的master文件
# 如果需要有同名文件覆盖功能，到代码里去找调用afni的部分加一个参数
base_session="ses-01"

MASTER_FILE="${root_dir}/data/${sub_name}/${base_session}/${sub_name}_dir-AP-seepi.nii.gz"

for subj in "${subj_list[@]}"
do
    sub_data_dir="${data_dir}/${subj}"
    echo "Processing ${subj} in ${sub_data_dir}..."
    
    ipython "$code_path_b0map" "$sub_data_dir" "$MASTER_FILE" "$sub_name"
done
# current calculation

ipython "$code_path_coil_shim" "$data_dir" "$sub_name" "${subj_list[*]}" "$base_session"