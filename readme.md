Global parameters store in Global_parameter.json

在Global_parameter.json中主要需要注意修改恒流源串口号、校准数据储存编号，目前默认是ses-01这种格式，只需要修改末位数字

需要加log功能，每次计算运行的结果用日期进行分类储存

在coil_control.py中修改wait_trigger的触发内容，改成真实trigger

用于 MRI EPI 8 通道 B0 匀场的实验性工作流：先标定各线圈的 B0 场响应，再计算 Global 或 Slice-wise 匀场电流，并通过串口下发至恒流源。

当前采用 Windows 控制硬件、Linux 虚拟机运行 AFNI 和 Python 计算的架构。Scanner Trigger 尚未接入；代码中的 input() / Read-Host 主要用于人工等待。
## 整体流程 ##
1. Windows：执行 RunEPIShim.ps1，调用 coil_control.py；依次设置 Baseline（8 通道 0 A）和 CH1～CH8 的标定电流，配合扫描。当前以 Enter 模拟切换触发。
2. 人工整理：标定结束后选择有效 Session、修改 Global_parameter.json，将 AP/PA 扫描数据及所需 JSON 放到对应目录，再按 Enter。
3. Linux：PowerShell 通过 SSH 调用 ShimCurrentCal.sh，用 AFNI 计算每个 Session 的 B0 map，再优化匀场电流。
4. Windows：计算结果写到 data/<subject>/shimming_currents.csv；`RunEPIShim.ps1` 等待人工确认后调用 CoilSliceControl.py，通过串口下发优化电流，并配合验证扫描。
当前 RunEPIShim.ps1 在调用 CoilSliceControl.py 之前有一次 Read-Host；`CoilSliceControl.py` 的 Global 模式在真正下发电流之前还会 input() 等待一次，因此存在两次开始确认。

2. 文件功能
项目根目录
文件	功能
RunEPIShim.ps1	Windows 总入口：先执行标定控制，等待整理数据；经 SSH 启动 Linux 计算；最后调用匀场电流下发程序。
Global_parameter.json	统一配置：受试者、有效 Session、匀场模式、串口、标定电流、限流值及数据路径等。
pyproject.toml	uv 使用的 Python 版本要求和依赖声明。
uv.lock	锁定 Python 依赖版本，以便复现环境。
readme.md / README.md	项目使用和文件说明。


code/ 目录
文件	功能
coil_control.py	标定时恒流源控制：按 Baseline、CH1～CH8 的顺序切换电流；当前用 Enter 模拟扫描触发。
CoilSliceControl.py	匀场控制：读取 shimming_currents.csv；Global 模式下一次施加 8 通道电流，Slice-wise 模式下逐层切换（目前手动按 Enter）。
ShimCurrentCal.sh	Linux 计算入口：读取配置与 Session，依次运行两个计算 Notebook。
B0mapCal.ipynb	使用 AFNI 处理 AP/PA EPI，依据 TotalReadoutTime 计算、重采样并掩膜 B0 场图（Hz）。
CoilSliceCurrentCalculation.ipynb	由 Baseline 和线圈标定 B0 图计算 Hz/A 基函数，使用 scipy.optimize.lsq_linear 优化 Global 或 Slice-wise 电流，输出 CSV（A）。
config.py	加载、校验 Global_parameter.json；解析数据路径、Session 和电流限制；提供单位换算。
dacControl.py	编码与发送 8 通道带符号电流 DAC 串口指令；检查电流上下限；提供全部通道归零函数。
serialSession.py	管理恒流源串口连接、启动反馈监控，并在会话退出时尝试将所有通道归零。
serialMonitor.py	接收与解析恒流源的串口反馈，后台输出状态。
testCOM.py	测试恒流源串口接收与反馈帧读取。
test_shim_pipeline.py	离线回归测试：检查标定归一化、优化、CSV 单位转换和 DAC 编码；不连接 MRI/恒流源。
GetInfoB0Map.ipynb	借助 AFNI 3dinfo 查看 NIfTI 图像的维度、体素大小、方向等信息。
CalculateCoilLength.ipynb	用几何公式计算线圈制作所需长度，不参与自动匀场流程。
trans.py	用 SimpleITK 做参考结构图与被试结构图的配准及基函数场重采样；目前不是主流程的一部分。


3. 数据组织与关键参数
示例目录（文件名以当前 Notebook 读取规则为准）：
data/
└── sub-03/
    ├── ses-01/                       # Baseline
    │   ├── sub-03_dir-AP-seepi.nii.gz
    │   ├── sub-03_dir-PA-seepi.nii.gz
    │   └── sub-03_dir-AP-seepi.json
    ├── ses-02/                       # CH1 对应的有效扫描
    ├── ...
    └── shimming_currents.csv         # 最终优化电流，单位 A
- data.subject：当前受试者，如 sub-03。
- shim.sessions：第一个为 Baseline，其后 8 个按顺序对应 CH1～CH8；编号可以不连续。
- shim.mode：`global`、`slice_wise_tra`、`slice_wise_cor`、`slice_wise_sag`。
- current.basis_scan_A 与 current.reference_A：标定电流及参考电流，当前分别为 0.5 A 与 0 A。
- serial.port：Windows 主机上恒流源所在串口，当前配置为 COM11。
- control.csv_current_unit：当前为 A；下发 DAC 前转换成整数 mA。
4. 运行环境
- Windows：用于运行 RunEPIShim.ps1、`coil_control.py`、`CoilSliceControl.py`，需有可访问恒流源串口的 Python 和 pyserial。
- Linux 虚拟机：需安装 AFNI、`uv` 和项目 Python 依赖；Windows 通过 SSH 调用 code/ShimCurrentCal.sh。
- 共享数据：应确保 Windows 和 Linux 访问到本次扫描使用的同一份 Global_parameter.json 和 data/，并确认 SSH 非交互环境能找到 AFNI 命令。
- VMware 共享目录 /mnt/hgfs/ 可能不支持虚拟环境所需符号链接；可将 Linux 的 uv 环境放到本机目录，如 /home/muzixi/.venvs/EPI_shim。
Linux 首次同步依赖（在项目根目录）：
UV_PROJECT_ENVIRONMENT="$HOME/.venvs/EPI_shim" uv sync
Windows 启动（PowerShell，在项目根目录）：
.\RunEPIShim.ps1
5. Trigger 与待完成事项
- 目前没有接入真正的 Scanner Trigger。`coil_control.py` 通过 wait_trigger() 中的 input() 等待标定切换；`CoilSliceControl.py` 通过 input() 等待 Global 开始/结束或 Slice-wise 逐层切换。
- 人工筛选数据的 PowerShell Read-Host 应保留，不需要改成 Scanner Trigger。
- Slice-wise 动态控制需确认扫描仪是否能输出逐层 TTL Trigger、实际采集顺序（包括交错/Multiband）、触发相对于 RF 的时刻，以及串口传输和恒流源稳定时间。每层采集不等于一定向外部输出逐层 Trigger。
- 当前 Slice-wise 代码在下发最后一层电流后会退出并尝试归零，尚缺少最后一层采集完成后的明确等待。