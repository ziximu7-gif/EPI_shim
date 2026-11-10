# 需要检查串口号是否正确
# 目前发出命令同一轮次命令之间没有间隔，如果出现问题可以加一个短等待
from serialMonitor import SerialMonitor

from config import get_setting
from dacControl import send_dac_cmd
from serialSession import coil_session

def wait_trigger() -> None:
    print("WAIT_TRIGGER: press ENTER...")
    input()
    print("WAIT_TRIGGER: got trigger")

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

    with coil_session(cfg) as ser:
        for rep, actions in enumerate(schedule):
            wait_trigger()
            sent_cmds = []
            for ch, direction, mA in actions:
                signed_mA = mA if direction == 1 else -mA
                cmd = send_dac_cmd(ser, ch, signed_mA)
                sent_cmds.append(cmd.hex(" ").upper())
            print(f"[rep={rep}] TX cmds: {sent_cmds}")

if __name__ == "__main__":
    main()