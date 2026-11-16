$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 1. Windows: coil calibration
python .\code\coil_control.py
if ($LASTEXITCODE -ne 0) {
    throw "Coil calibration failed"
}

# 2. Wait for data preparation
Read-Host "标定扫描结束。请选择数据、修改 JSON、复制文件，完成后按 Enter"

# 3. Linux: run AFNI and shim calculation through SSH
$LinuxHost = "muzixi@192.168.233.128"
$LinuxRepo = "/home/muzixi/MRI_Share/ECC_Project/EPI_shim"

$remoteCommand = 'cd "' + $LinuxRepo + '" || exit 1; export PATH="/home/muzixi/abin:$PATH"; export UV_PROJECT_ENVIRONMENT="/home/muzixi/.venvs/EPI_shim"; /home/muzixi/.local/bin/uv run bash code/ShimCurrentCal.sh'

ssh $LinuxHost $remoteCommand

if ($LASTEXITCODE -ne 0) {
    throw "Linux calculation failed"
}

# 4. Windows: apply shim currents
Write-Host "计算完成，准备进入匀场控制"

Read-Host "准备进入匀场控制，按 Enter 启动电流控制程序"

python .\code\CoilSliceControl.py

if ($LASTEXITCODE -ne 0) {
    throw "Shim current control failed"
}

Write-Host "Workflow finished"
