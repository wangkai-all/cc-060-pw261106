"""时间线仿真：给定一辆车的订单序列，推算每站到离时刻与回库时刻，并检查可行性。

规则：
- 车辆 shift_start 时刻从仓库出发；
- 早到允许等待，服务开始 = max(到达, 时窗起)，且必须 <= 时窗止；
- 服务结束后立即前往下一站；最后回库，回库时刻必须 <= shift_end；
- 单车单趟：初始装载 = 全部订单之和，不得超过载重/容积。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from models import Order, Problem, Vehicle


@dataclass
class StopSim:
    order_id: str
    node: int
    leg_km: float       # 上一站到本站的里程
    leg_sec: int        # 上一站到本站的行驶秒
    arrival: int
    service_start: int
    service_end: int
    wait: int           # 早到等待秒数


@dataclass
class RouteSim:
    vehicle_id: str
    stops: list[StopSim]
    depart_depot: int
    return_depot: int
    back_km: float
    back_sec: int
    feasible: bool
    reason: str = ""

    @property
    def total_sec(self) -> int:
        return sum(s.leg_sec for s in self.stops) + self.back_sec

    @property
    def total_km(self) -> float:
        return sum(s.leg_km for s in self.stops) + self.back_km


def load_of(orders: list[Order]) -> tuple[float, float]:
    return sum(o.weight for o in orders), sum(o.volume for o in orders)


def simulate(vehicle: Vehicle, orders: list[Order], prob: Problem) -> RouteSim:
    """仿真一条路线；infeasible 时给出首个原因。"""
    tm, dm, depot = prob.time_matrix, prob.dist_matrix, prob.depot
    stops: list[StopSim] = []
    t = vehicle.shift_start
    prev = depot
    for o in orders:
        leg_sec = tm[prev][o.node]
        leg_km = dm[prev][o.node]
        arrival = t + leg_sec
        svc_start = max(arrival, o.tw_start)
        if svc_start > o.tw_end:
            return RouteSim(vehicle.id, stops, vehicle.shift_start, 0, 0.0, 0,
                            False, f"订单 {o.name} 到达过晚：服务开始将晚于承诺时窗止")
        svc_end = svc_start + o.service
        stops.append(StopSim(o.id, o.node, leg_km, leg_sec, arrival,
                             svc_start, svc_end, svc_start - arrival))
        t = svc_end
        prev = o.node
    back_sec = tm[prev][depot]
    back_km = dm[prev][depot]
    ret = t + back_sec
    if ret > vehicle.shift_end:
        return RouteSim(vehicle.id, stops, vehicle.shift_start, ret, back_km, back_sec,
                        False, "回库时刻晚于班次结束")
    return RouteSim(vehicle.id, stops, vehicle.shift_start, ret, back_km, back_sec, True)


def capacity_ok(vehicle: Vehicle, orders: list[Order]) -> tuple[bool, str]:
    w, v = load_of(orders)
    if w > vehicle.cap_weight + 1e-9:
        return False, f"超载：{w:.1f}kg > {vehicle.cap_weight:.1f}kg"
    if v > vehicle.cap_volume + 1e-9:
        return False, f"超容积：{v:.2f}m³ > {vehicle.cap_volume:.2f}m³"
    return True, ""


def route_feasible(vehicle: Vehicle, orders: list[Order], prob: Problem) -> tuple[bool, str]:
    ok, why = capacity_ok(vehicle, orders)
    if not ok:
        return False, why
    sim = simulate(vehicle, orders, prob)
    return sim.feasible, sim.reason
