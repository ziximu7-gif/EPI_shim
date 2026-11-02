# 需要检查串口号是否正确
# 目前发出命令同一轮次命令之间没有间隔，如果出现问题可以加一个短等待
import time
import serial
import threading
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


def make_dac_cmd(channel: int, direction: int, current_mA: int) -> str:
    if not (1 <= channel <= 8):
        raise ValueError("channel must be 1..8")
    if direction not in (0, 1):
        raise ValueError("direction must be 0 or 1")
    if not (0 <= current_mA <= 2000):
        raise ValueError("current_mA out of range (0..2000)")
    return f"BDAC{channel}{direction}{current_mA:04X}OV"


def send_cmd(ser: serial.Serial, cmd: str) -> None:
    ser.write(cmd.encode("ascii"))


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
    PORT = "COM11"
    BAUD = 115200
    current_test = 500

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

            # 触发到来：先清空旧数据（你要求的）
            mon.flush()

            sent_cmds = []
            for (ch, direction, mA) in actions:
                cmd = make_dac_cmd(ch, direction, mA)
                send_cmd(ser, cmd)
                sent_cmds.append(cmd)
                # 如需命令间隔：
                # time.sleep(0.002)

            print(f"[rep={rep}] TX cmds: {sent_cmds}")

        mon.stop()


if __name__ == "__main__":
    main()