# 需要检查串口号是否正确
# 目前发出命令同一轮次命令之间没有间隔，如果出现问题可以加一个短等待
import time
import serial
import threading
from dataclasses import dataclass
from typing import Iterator, Optional, List

from config import get_setting
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

def wait_trigger() -> None:
    print("WAIT_TRIGGER: press ENTER...")
    input()
    print("WAIT_TRIGGER: got trigger")


class SerialMonitor:
    """
    Background reader: continuously reads frames and prints them.
    Supports 'flush' to ignore old frames after a trigger.
    """
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
        # clear driver buffer + tell reader to ignore partial leftovers
    
        self.ser.reset_input_buffer()
        self.flush_evt.set()

    def _run(self):
        while not self.stop_evt.is_set():
            try:
                frame = next(self.frames)  # blocks until a frame arrives
            except Exception as e:
                print("MONITOR ERROR:", e)
                return

            # if we just flushed, drop the first frame(s) until stable
            if self.flush_evt.is_set():
                # drop exactly one frame after flush (tunable)
                self.flush_evt.clear()
                continue

            st = parse_status_frame(frame)
            ts = time.perf_counter()
            if st is None:
                print(f"{ts:.6f} RX RAW: {frame!r}")
            else:
                print(f"{ts:.6f} RX STATUS: ch={st.ch} status={st.status}")


def main():
    cfg = get_setting()
    PORT = cfg["serial"]["port"]
    BAUD = cfg["serial"]["baudrate"]
    current_test = int(cfg["current"]["test"]["single_channel_mA"])

    schedule = [
        [(1, 1, 0)],
        [(1, 1, current_test)],
        [(1, 1, 0), (2, 1, current_test)],
        [(2, 1, 0), (3, 1, current_test)],
        [(3, 1, 0), (4, 1, current_test)],
        [(4, 1, 0), (5, 1, current_test)],
        [(5, 1, 0), (6, 1, current_test)],
        [(6, 1, 0), (7, 1, current_test)],
        [(7, 1, 0), (8, 1, current_test)],
    ]

    with serial.Serial(PORT, baudrate=BAUD, timeout=0.2) as ser:
        mon = SerialMonitor(ser)
        mon.start()

        for rep, actions in enumerate(schedule):
            wait_trigger()

            sent_cmds = []
            for (ch, direction, mA) in actions:
                signed_mA = mA if direction == 1 else -mA
                cmd = send_dac_cmd(ser, ch, signed_mA)
                sent_cmds.append(cmd.hex(" ").upper())

            print(f"[rep={rep}] TX cmds: {sent_cmds}")

        mon.stop()


if __name__ == "__main__":
    main()