"""领域模型：车辆、订单、问题实例。时间统一用"午夜起算的秒"(int)，距离用公里(float)。"""
from __future__ import annotations

from dataclasses import dataclass, field

# 订单状态
PENDING = "pending"        # 待排
ASSIGNED = "assigned"      # 已排入某车
UNASSIGNED = "unassigned"  # 启发式未能分配（不证明无解）
CANCELED = "canceled"      # 回放中被取消
DONE = "done"              # 已完成服务（回放语义）


@dataclass
class Node:
    id: int
    name: str
    x: float
    y: float


@dataclass
class Vehicle:
    id: str
    name: str
    cap_weight: float      # kg
    cap_volume: float      # m^3
    shift_start: int       # 秒，从午夜起算
    shift_end: int
    activation_fee: float  # 启用费（出车才收）
    cost_per_km: float     # 每公里费用


@dataclass
class Order:
    id: str
    name: str
    node: int
    weight: float
    volume: float
    service: int    # 服务时长（秒）
    tw_start: int   # 承诺时窗起（秒）
    tw_end: int     # 承诺时窗止（秒）
    status: str = PENDING
    reason: str = ""        # 未分配原因
    cancel_time: int | None = None  # 取消发生的回放时刻


@dataclass
class Problem:
    nodes: list[Node]
    time_matrix: list[list[int]]    # time_matrix[i][j]: i->j 行驶秒数（可非对称）
    dist_matrix: list[list[float]]  # dist_matrix[i][j]: i->j 公里数（可非对称）
    vehicles: list[Vehicle]
    orders: list[Order]
    depot: int = 0

    def order_map(self) -> dict[str, Order]:
        return {o.id: o for o in self.orders}

    def vehicle_map(self) -> dict[str, Vehicle]:
        return {v.id: v for v in self.vehicles}


def fmt_time(sec: int | float | None) -> str:
    if sec is None:
        return "-"
    sec = int(round(sec))
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def hhmm(h: int, m: int = 0) -> int:
    return h * 3600 + m * 60
