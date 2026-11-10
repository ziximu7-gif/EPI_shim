"""恒流源串口反馈解析与后台监控。"""
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

def iter_frames(ser, stop_evt=None):
    """从串口字节流中提取 B...OV 反馈帧。"""
    if stop_evt is None:
        stop_evt = threading.Event()
    buf = bytearray()
    while not stop_evt.is_set():
        chunk = ser.read(4096)
        # 串口超时、暂时没有数据
        if not chunk:
            continue
        buf.extend(chunk)
        while not stop_evt.is_set():
            try:
                start = buf.index(ord("B"))
            except ValueError:
                buf.clear()
                break
            end = buf.find(b"OV", start + 1)
            if end < 0:
                if start > 0:
                    del buf[:start]
                break
            frame = bytes(buf[start:end + 2])
            del buf[:end + 2]
            yield frame

@dataclass
class StatusFrame:
    ch: List[int]
    status: int

def parse_status_frame(frame: bytes) -> Optional[StatusFrame]:
    """
    解析当前旧格式反馈。
    TODO: 根据恒流源实际反馈数据，
    更新温度、通道状态及模式状态等字段。
    """
    try:
        s = frame.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return None
    if not (s.startswith("B") and s.endswith("OV")):
        return None
    body = s[1:-2]
    # 暂时保留原有的38字符解析逻辑
    if len(body) != 38 or not body.isdigit():
        return None
    ch = [
        int(body[i:i + 4])
        for i in range(0, 32, 4)
    ]
    status = int(body[32:38])
    return StatusFrame(ch=ch, status=status)

class SerialMonitor:
    """在后台线程中持续显示串口反馈。"""
    def __init__(self, ser):
        self.ser = ser
        self.stop_evt = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
        )
    def start(self):
        self.thread.start()
    def stop(self):
        self.stop_evt.set()
        self.thread.join(timeout=1.0)

        if self.thread.is_alive():
            print("WARNING: SerialMonitor did not stop")
    def _run(self):
        try:
            for frame in iter_frames(self.ser, self.stop_evt):
                if self.stop_evt.is_set():
                    break
                ts = time.perf_counter()
                st = parse_status_frame(frame)
                if st is None:
                    print(f"{ts:.6f} RX RAW: {frame!r}")
                else:
                    print(
                        f"{ts:.6f} RX STATUS: "
                        f"ch={st.ch} status={st.status}"
                    )
        except Exception as exc:
            if not self.stop_evt.is_set():
                print("MONITOR ERROR:", exc)
