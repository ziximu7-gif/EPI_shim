#还需要修改
import time
import serial
import threading
import csv
from dataclasses import dataclass
from typing import Iterator, Optional, List

from config import get_setting, get_current_limits_mA, on_overflow_mode, paths

def iter_frames(ser: serial.Serial) -> Iterator[bytes]:
    buf = bytearray()
    while True:
        buf += ser.read(4096)
        while True:
            try:
                s = buf.index(ord("B"))
            except ValueError:
                buf.clear()
                break
            e = buf.find(b"OV", s + 1)
            if e < 0:
                if s > 0:
                    del buf[:s]
                break
            frame = bytes(buf[s:e+2])
            del buf[:e+2]
            yield frame

@dataclass
class StatusFrame:
    ch: List[int]
    status: int

def parse_status_frame(frame: bytes) -> Optional[StatusFrame]:
    try:
        s = frame.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return None
    if not (s.startswith("B") and s.endswith("OV")):
        return None
    body = s[1:-2]
    if len(body) != 38 or (not body.isdigit()):
        return None
    ch = [int(body[i:i+4]) for i in range(0, 32, 4)]
    status = int(body[32:38])
    return StatusFrame(ch=ch, status=status)

# ============================================================
# 电流幅值上限 —— 统一来自项目根目录 Global_parameter.json 的 current.limits
# 修改限值请改 Global_parameter.json，不要改这里。
# 协议限制：BDAC + 通道(1位) + 方向(1位) + 数值(4位十六进制) + OV
#   数值字段为 %04X，因此硬上限不得超过 4095 mA，否则命令长度越界。
#   （config.py 在加载时已校验这一点）
# 超限处理由 Global_parameter.json 的 current.on_overflow 决定：
#   "raise" -> 直接报错（默认，避免“以为发了 3A 实际只发了 2A”）
#   "clip"  -> 静默截断到上限
# ============================================================
def _limit_ma_for_direction(direction: int) -> int:
    """正/负方向各自的幅值上限（mA），来自 Global_parameter.json 的 current.limits。"""
    lower_ma, upper_ma = get_current_limits_mA()
    return upper_ma if direction == 1 else abs(lower_ma)


def make_dac_cmd(channel: int, current_mA: int) -> str:
    """
    channel: 1..8
    current_mA: 这里的输入已经是经过转换后的整数 mA
    """
    if not (1 <= channel <= 8):
        raise ValueError("channel must be 1..8")

    direction = 1 if current_mA >= 0 else 0
    val = abs(current_mA)

    limit_ma = _limit_ma_for_direction(direction)
    if val > limit_ma:
        if on_overflow_mode() == "clip":
            val = limit_ma
        else:
            key = "positive_A" if direction == 1 else "negative_A"
            raise ValueError(
                f"ch{channel}: |current| = {val} mA exceeds limit {limit_ma} mA "
                f"(from Global_parameter.json: current.limits.{key}; "
                f"set current.on_overflow=\"clip\" to clamp instead)"
            )

    return f"BDAC{channel}{direction}{val:04X}OV"

class SerialMonitor:
    def __init__(self, ser: serial.Serial):
        self.ser = ser
        self.frames = iter_frames(ser)
        self.stop_evt = threading.Event()
        self.flush_evt = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_evt.set()
        self.thread.join(timeout=1.0)

    def flush(self):
        self.ser.reset_input_buffer()
        self.flush_evt.set()

    def _run(self):
        while not self.stop_evt.is_set():
            try:
                frame = next(self.frames)
            except Exception as e:
                print("MONITOR ERROR:", e)
                return
            if self.flush_evt.is_set():
                self.flush_evt.clear()
                continue
            st = parse_status_frame(frame)
            ts = time.perf_counter()
            if st is None:
                print(f"{ts:.6f} RX RAW: {frame!r}")
            else:
                print(f"{ts:.6f} RX STATUS: ch={st.ch} status={st.status}")


def load_shimming_data(file_path: str):
    """
    加载 CSV 数据。
    CSV 结构：
    第 1 行：编号 (跳过)
    第 2-9 行：通道 1-8 的电流值 (单位由 Global_parameter.json 的 control.csv_current_unit 指定，
              目前为 A)
    返回格式：List[List[int]] -> 每层 8 个以 mA 为单位的电流值
    """
    cfg = get_setting()
    slice_count = int(cfg["control"]["slice_count"])
    csv_unit_is_A = str(cfg["control"].get("csv_current_unit", "A")).upper() == "A"
    scale = 1000.0 if csv_unit_is_A else 1.0

    # 初始化各层，每层准备存放 8 个通道的数据
    layers_data = [[] for _ in range(slice_count)]

    with open(file_path, 'r') as f:
        reader = list(csv.reader(f))
        for row in reader[1:9]:
            for layer_idx, val in enumerate(row):
                if layer_idx < slice_count:
                    current_mA = round(float(val) * scale)
                    layers_data[layer_idx].append(current_mA)

    return layers_data

# --- 主程序 ---

def main():
    cfg = get_setting()
    PORT = cfg["serial"]["port"]
    BAUD = cfg["serial"]["baudrate"]
    slice_count = int(cfg["control"]["slice_count"])
    CSV_FILE = str(paths(cfg).shimming_csv())

    # 1. 加载电流数据
    shimming_layers = load_shimming_data(CSV_FILE)
    print(f"Successfully loaded {len(shimming_layers)} layers from {CSV_FILE}")

    # 2. 启动串口和监控
    with serial.Serial(PORT, baudrate=BAUD, timeout=cfg["serial"]["timeout_s"]) as ser:
        mon = SerialMonitor(ser)
        mon.start()

        print("\n--- Dynamic Shimming Control Started ---")

        for idx, currents in enumerate(shimming_layers):
            print(f"\n[Layer {idx+1}/{slice_count}] Ready to switch.")
            print("Press ENTER to send currents, or Ctrl+C to quit...")
            input() 

            # 清空缓存
            mon.flush()

            # 构造并发送该层所有 8 个通道的命令
            print(f"Sending currents for Layer {idx+1}: {currents}")
            for ch_idx, val in enumerate(currents):
                channel = ch_idx + 1 # 通道从 1 开始
                cmd = make_dac_cmd(channel, val)
                ser.write(cmd.encode("ascii"))
                # 如果底层单片机处理较慢，建议取消注释下面这行
                # time.sleep(0.005) 

        print(f"\nAll {slice_count} layers completed.")
        mon.stop()

if __name__ == "__main__":
    main()