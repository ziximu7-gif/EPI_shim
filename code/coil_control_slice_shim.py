#还没完全写好
import time
import serial
import threading
import csv
from dataclasses import dataclass
from typing import Iterator, Optional, List

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
    raw: str

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
    return StatusFrame(ch=ch, status=status, raw=s)

def make_dac_cmd(channel: int, current_mA: int) -> str:
    """
    channel: 1..8
    current_mA: 这里的输入已经是经过转换后的整数 mA
    """
    direction = 1 if current_mA >= 0 else 0
    val = abs(current_mA)
    if val > 2000:
        val = 2000
        
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
            except:
                return
            if self.flush_evt.is_set():
                self.flush_evt.clear()
                continue
            st = parse_status_frame(frame)
            if st:
                ts = time.perf_counter()


def load_shimming_data(file_path: str):
    """
    加载 CSV 数据。
    CSV 结构：
    第 1 行：编号 (跳过)
    第 2-9 行：通道 1-8 的电流值 (单位 A)
    返回格式：List[List[float]] -> 72层，每层8个以 mA 为单位的电流值
    """
    # 初始化 72 层，每层准备存放 8 个通道的数据
    layers_data = [[] for _ in range(72)] 
    
    with open(file_path, 'r') as f:
        reader = list(csv.reader(f))
        for row in reader[1:9]:
            for layer_idx, val in enumerate(row):
                if layer_idx < 72:
                    current_mA = round(float(val) * 1000)
                    layers_data[layer_idx].append(current_mA)
                    
    return layers_data

# --- 主程序 ---

def main():
    PORT = "COM11"  
    BAUD = 115200
    CSV_FILE = r"..\data\sub-03\shimming_currents.csv"

    # 1. 加载电流数据
    shimming_layers = load_shimming_data(CSV_FILE)
    print(f"Successfully loaded {len(shimming_layers)} layers.")

    # 2. 启动串口和监控
    with serial.Serial(PORT, baudrate=BAUD, timeout=0.2) as ser:
        mon = SerialMonitor(ser)
        mon.start()

        print("\n--- Dynamic Shimming Control Started ---")
        
        for idx, currents in enumerate(shimming_layers):
            print(f"\n[Layer {idx+1}/72] Ready to switch.")
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

        print("\nAll 72 layers completed.")
        mon.stop()

if __name__ == "__main__":
    main()