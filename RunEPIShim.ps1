$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python .\code\coil_control.py
if ($LASTEXITCODE -ne 0) {
    throw "恒流源控制程序执行失败"
}
Read-Host "扫描结束。请选择数据、修改 JSON、复制文件，完成后按 Enter"

$LinuxHost = "muzixi@192.168.233.128"
$LinuxRepo = "/home/muzixi/MRI_Share/ECC_Project/EPI_shim"
ssh $LinuxHost "cd '$LinuxRepo' && UV_PROJECT_ENVIRONMENT=/home/muzixi/.venvs/EPI_shim /home/muzixi/.local/bin/uv run bash code/ShimCurrentCal.sh"
if ($LASTEXITCODE -ne 0) {
    throw "Linux 计算失败"
}
Write-Host "计算完成，匀场电流储存在 shimming_currents.csv"
Read-Host "WAIT_TRIGGER：按 Enter 开始匀场"
python .\code\CoilSliceControl.py
