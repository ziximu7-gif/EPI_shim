"""
EPI_shim 统一配置加载模块。

所有可调参数（数据路径 / 电流 / 串口 / 算法）的唯一来源是
项目根目录的 Global_parameter.json。
本模块负责：
  1. 定位并读取 Global_parameter.json（无需关心当前工作目录）
  2. 提供单位换算：A <-> mA
  3. 提供限幅工具：把任意电流裁到 [negative_A, positive_A]
  4. 解析数据目录与文件名（被 notebook 与下发脚本共用）

用法（在 code/ 下的脚本或 notebook 中）::

    from config import get_setting, A2mA, clip_current_A, paths

    cfg = get_setting()
    port = cfg["serial"]["port"]
    p = paths()          # 已解析好的绝对路径

配置文件路径可用环境变量 EPI_SHIM_SETTING 覆盖（便于在服务器上的另一份数据目录使用）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

# 允许用环境变量指定其它配置文件
ENV_VAR = "EPI_SHIM_SETTING"

# 配置文件固定名称（不要改成 setting.json，全项目只保留这一份）
CONFIG_FILENAME = "Global_parameter.json"

_cached_path: Optional[Path] = None
_cached_cfg: Optional[Dict[str, Any]] = None


def find_setting_file(start: Optional[Path] = None) -> Path:
    """
    定位 Global_parameter.json：优先环境变量，然后从 start（默认本文件目录）
    逐级向上查找，直到磁盘根目录。
    """
    env_path = os.environ.get(ENV_VAR)
    if env_path:
        p = Path(env_path).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"{ENV_VAR} points to a missing file: {p}")
        return p

    here = Path(start).resolve() if start else Path(__file__).resolve().parent
    for candidate_dir in (here, *here.parents):
        candidate = candidate_dir / CONFIG_FILENAME
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(
        f"{CONFIG_FILENAME} not found. Searched upward from {here}. "
        f"Create it at the project root, or set the {ENV_VAR} environment variable."
    )


def get_setting(reload: bool = False) -> Dict[str, Any]:
    """读取并缓存 Global_parameter.json。reload=True 强制重新读取。"""
    global _cached_path, _cached_cfg

    path = find_setting_file()
    if _cached_cfg is not None and not reload and path == _cached_path:
        return _cached_cfg

    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    _validate(cfg, path)

    _cached_path = path
    _cached_cfg = cfg
    return cfg


def setting_path() -> Optional[Path]:
    """返回当前生效的配置文件路径（未加载过则为 None）。"""
    return _cached_path


def project_root() -> Path:
    """返回项目根目录（即 Global_parameter.json 所在目录的父目录若在 code/ 下）。"""
    p = _cached_path or find_setting_file()
    return p.parent


def _validate(cfg: Dict[str, Any], path: Path) -> None:
    """尽早暴露配置错误，避免硬件被错误驱动。"""
    try:
        limits = cfg["current"]["limits"]
        pos = float(limits["positive_A"])
        neg = float(limits["negative_A"])
    except KeyError as e:
        raise KeyError(f"{path}: missing required key {e} under current.limits") from None

    if pos <= 0 or neg <= 0:
        raise ValueError(
            f"{path}: current.limits must be positive (got positive_A={pos}, negative_A={neg})"
        )

    protocol_max = float(cfg.get("current", {}).get("protocol", {}).get("max_mA", 4095))
    if max(pos, neg) * 1000.0 > protocol_max:
        raise ValueError(
            f"{path}: current limit {max(pos, neg)} A exceeds DAC protocol maximum "
            f"{protocol_max} mA (4 hex digits)."
        )

    if cfg["current"].get("on_overflow", "raise") not in ("raise", "clip"):
        raise ValueError(
            f"{path}: current.on_overflow must be 'raise' or 'clip', "
            f"got {cfg['current'].get('on_overflow')!r}"
        )


# ---------------- 数据路径解析 ----------------

class Paths:
    """由 Global_parameter.json 的 data 段解析出的绝对路径集合。"""

    def __init__(self, cfg: Dict[str, Any], root: Path):
        d = cfg.get("data", {})

        raw_root = Path(d.get("root_dir", "./data")).expanduser()
        self.data_root = raw_root if raw_root.is_absolute() else (root / raw_root)
        self.data_root = self.data_root.resolve()

        self.subject = str(d.get("subject", "sub-03"))
        self.subject_dir = self.data_root / self.subject

        self.b0map_file = str(d.get("b0map_masked_file", "B0_map_Hz_to_master_masked.nii.gz"))
        self.csv_name = str(d.get("shimming_currents_csv", "shimming_currents.csv"))

        shim = cfg.get("shim", {})
        self.base_session = str(shim.get("base_session", "ses-01"))
        self.sessions = [f"ses-{int(i):02d}" for i in shim.get("sessions", [])]
        # 线圈扫描 session（去掉基准 session）
        self.coil_sessions = [s for s in self.sessions if s != self.base_session]

    def b0map(self, session: str) -> Path:
        """某个 session 的 B0 图路径。"""
        return self.subject_dir / session / self.b0map_file

    def shimming_csv(self) -> Path:
        """shimming_currents.csv 的默认落盘位置。"""
        return self.subject_dir / self.csv_name

    def __repr__(self) -> str:
        return (
            f"Paths(subject={self.subject!r}, data_root={str(self.data_root)!r}, "
            f"base_session={self.base_session!r}, sessions={len(self.sessions)})"
        )


def paths(cfg: Optional[Dict[str, Any]] = None) -> Paths:
    """返回解析好的数据路径对象。"""
    cfg = cfg or get_setting()
    return Paths(cfg, project_root())


# ---------------- 单位换算 ----------------

def A2mA(current_a: float) -> int:
    """安培 -> 整数毫安（四舍五入）。"""
    return int(round(float(current_a) * 1000.0))


def mA2A(current_ma: float) -> float:
    """毫安 -> 安培。"""
    return float(current_ma) / 1000.0


# ---------------- 电流上下限 ----------------

def get_current_limits_A(cfg: Optional[Dict[str, Any]] = None):
    """返回 (lower_A, upper_A)，支持非对称。"""
    cfg = cfg or get_setting()
    limits = cfg["current"]["limits"]
    return -abs(float(limits["negative_A"])), abs(float(limits["positive_A"]))


def get_current_limits_mA(limit_ma: Optional[int] = None):
    """返回 (lower_mA, upper_mA)。limit_ma 给定时作为对称半幅值使用。"""
    if limit_ma is not None:
        half = abs(int(limit_ma))
        return -half, half
    lo, hi = get_current_limits_A()
    return A2mA(lo), A2mA(hi)


def clip_current_A(value: float, cfg: Optional[Dict[str, Any]] = None) -> float:
    """把电流裁到配置的上下限内（单位 A）。"""
    lo, hi = get_current_limits_A(cfg)
    return min(max(float(value), lo), hi)


def clip_current_mA(value_ma: float, cfg: Optional[Dict[str, Any]] = None) -> int:
    """把电流裁到配置的上下限内（单位 mA，返回整数）。"""
    lo, hi = get_current_limits_mA()
    return int(round(min(max(float(value_ma), lo), hi)))


def on_overflow_mode(cfg: Optional[Dict[str, Any]] = None) -> str:
    """返回 'raise' 或 'clip'。"""
    cfg = cfg or get_setting()
    return cfg["current"].get("on_overflow", "raise")


if __name__ == "__main__":
    cfg = get_setting()
    lo, hi = get_current_limits_A()
    p = paths()
    print(f"config file  : {setting_path()}")
    print(f"current range: {lo:+.3f} A .. {hi:+.3f} A "
          f"({A2mA(lo)} .. {A2mA(hi)} mA)")
    print(f"overflow mode: {on_overflow_mode()}")
    print(f"serial       : {cfg['serial']['port']} @ {cfg['serial']['baudrate']}")
    print(f"subject      : {p.subject}  (data root: {p.data_root})")
    print(f"sessions     : {' '.join(p.sessions)}  (base: {p.base_session})")
    print(f"shim mode    : {cfg['shim']['mode']}")