
"""
DAC 恒流源串口控制协议

协议格式：
BDAC + 通道号(ASCII) + 电流(int32, 小端序) + OV

电流单位：mA
支持正负电流
电流上限来自 Global_parameter.json
"""

from operator import index

from config import get_current_limits_mA, on_overflow_mode


def make_dac_cmd(channel: int, current_mA: int) -> bytes:
    """将通道号和有符号电流转换为 11 字节 DAC 指令。"""

    if isinstance(channel, bool) or not 1 <= index(channel) <= 8:
        raise ValueError("channel must be 1..8")

    # 接受整数，包括 numpy 整数；不允许浮点数被静默截断
    current_mA = index(current_mA)

    # 配置中的有符号上下限
    lower_ma, upper_ma = get_current_limits_mA()

    if not lower_ma <= current_mA <= upper_ma:
        if on_overflow_mode() == "clip":
            current_mA = max(lower_ma, min(current_mA, upper_ma))
        else:
            key = (
                "positive_A" if current_mA > upper_ma
                else "negative_A"
            )
            raise ValueError(
                f"ch{channel}: current={current_mA} mA "
                f"exceeds limits [{lower_ma}, {upper_ma}] mA "
                f"(Global_parameter.json: current.limits.{key})"
            )

    current_bytes = current_mA.to_bytes(
        4, byteorder="little", signed=True
    )

    return (
        b"BDAC"
        + str(channel).encode("ascii")
        + current_bytes
        + b"OV"
    )

def send_dac_cmd(ser, channel: int, current_mA: int) -> bytes:
    """生成指令并通过已经打开的串口发送。"""

    cmd = make_dac_cmd(channel, current_mA)
    ser.write(cmd)
    return cmd

def reset_all_currents(ser):
    """退出时尝试将全部8个通道设为0 mA。"""
    print("Resetting all channels to 0 mA...")
    errors = []
    for channel in range(1, 9):
        try:
            cmd = send_dac_cmd(ser, channel, 0)
            print(
                f"CH{channel}: 0 mA -> "
                f"{cmd.hex(' ').upper()}"
            )
        except Exception as exc:
            errors.append(channel)
            print(f"CH{channel} reset failed: {exc}")
    try:
        ser.flush()
    except Exception as exc:
        errors.append("serial_flush")
        print(f"Serial flush failed: {exc}")
    if errors:
        raise RuntimeError(
            f"Reset commands could not be completed: {errors}"
        )
