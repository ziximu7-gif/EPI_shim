#还需要修改
import time
import serial
import threading
import csv
from dataclasses import dataclass
from typing import Iterator, Optional, List

from config import get_setting, paths
from dacControl import send_dac_cmd

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
# 超限处理由 Global_parameter.json 的 current.on_overflow 决定：
#   "raise" -> 直接报错
#   "clip"  -> 静默截断到上限
# ============================================================

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
                channel = ch_idx + 1
                cmd = send_dac_cmd(ser, channel, val)
                print(f"CH{channel}: {val} mA -> "f"{cmd.hex(' ').upper()}")

        print(f"\nAll {slice_count} layers completed.")
        mon.stop()

if __name__ == "__main__":
    main()