$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$ValidationSession = "ses-AfterShim"
# 1. Windows: coil calibration
uv run python .\code\coil_control.py
if ($LASTEXITCODE -ne 0) {
    throw "Coil calibration failed"
}

# 2. Wait for data preparation
Read-Host "标定扫描结束。请选择数据、修改 JSON、复制文件，完成后按 Enter"

# 3. Linux: run AFNI and shim calculation through SSH
$LinuxHost = "muzixi@192.168.233.128"
$LinuxRepo = "/home/muzixi/MRI_Share/ECC_Project/EPI_shim"

$remotePrefix = 'cd "' + $LinuxRepo + '" || exit 1; export PATH="/home/muzixi/abin:$PATH"; export UV_PROJECT_ENVIRONMENT="/home/muzixi/.venvs/EPI_shim"; '
$remoteCommand = $remotePrefix + '/home/muzixi/.local/bin/uv run bash code/ShimCurrentCal.sh'
ssh $LinuxHost $remoteCommand

if ($LASTEXITCODE -ne 0) {
    throw "Linux calculation failed"
}

# 4. Windows: apply shim currents
Write-Host "计算完成，准备进入匀场控制"

Read-Host "准备进入匀场控制，按 Enter 启动电流控制程序"

uv run python .\code\CoilSliceControl.py

if ($LASTEXITCODE -ne 0) {
    throw "Shim current control failed"
}

Write-Host "Workflow finished"

$validationFolder = "data/$subject/$ValidationSession"
Write-Host "匀场后扫描结束。请将本次 AP/PA EPI 数据和 AP JSON 手动复制到："
Write-Host "  $validationFolder"
Read-Host "确认数据已复制且文件名正确，按 Enter 生成匀场前后对比报告"

$validationCommand = $remotePrefix +
    '/home/muzixi/.local/bin/uv run bash code/ShimValidation.sh ' +
    $ValidationSession

ssh $LinuxHost $validationCommand

if ($LASTEXITCODE -ne 0) {
    throw "Linux post-shim validation failed; inspect the ses-AfterShim data files"
}