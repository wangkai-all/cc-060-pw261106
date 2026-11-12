"""计划结构：把求解结果展开成每车的站点、路段与事件流，供回放与持久化。"""
from __future__ import annotations

from dataclasses import dataclass, field

from models import Problem
from solver import Solution
from timeline import simulate

# 站点状态
ST_PLANNED = "planned"            # 计划服务
ST_DONE = "done"                  # 服务已完成
ST_SERVING = "serving"            # 服务进行中（固定）
ST_CANCEL_VISIT = "canceled_visit"  # 取消但仍完成该段行驶，到点不卸货
ST_CANCEL_SKIP = "canceled_skip"    # 取消且不再前往（货物仍在车上直到回库）


@dataclass
class StopPlan:
    order_id: str
    node: int
    status: str = ST_PLANNED
    arrival: int | None = None
    service_start: int | None = None
    service_end: int | None = None
    depart: int | None = None
    wait: int = 0
    cargo_aboard: bool = False   # 已取消但货物占容量直到回库


@dataclass
class Leg:
    frm: int
    to: int
    depart: int
    arrive: int
    km: float
    sec: int


@dataclass
class VehiclePlan:
    vehicle_id: str
    stops: list[StopPlan] = field(default_factory=list)
    legs: list[Leg] = field(default_factory=list)
    depart_depot: int | None = None
    return_depot: int | None = None
    km: float = 0.0
    sec: int = 0
    cost: float = 0.0


@dataclass
class Plan:
    kind: str                              # initial | improved | replan
    vehicles: dict[str, VehiclePlan] = field(default_factory=dict)
    unassigned: dict[str, str] = field(default_factory=dict)
    canceled: list[str] = field(default_factory=list)
    pause_time: int | None = None
    note: str = ""

    def metrics(self, prob: Problem) -> dict:
        vmap = prob.vehicle_map()
        delivered = sum(1 for vp in self.vehicles.values()
                        for s in vp.stops if s.status in (ST_PLANNED, ST_DONE, ST_SERVING))
        used = sum(1 for vp in self.vehicles.values() if vp.legs)
        travel = sum(vp.sec for vp in self.vehicles.values())
        km = sum(vp.km for vp in self.vehicles.values())
        cost = sum(vp.cost for vp in self.vehicles.values())
        return {"delivered": delivered, "vehicles": used, "travel_sec": travel,
                "distance_km": round(km, 3), "cost": round(cost, 2),
                "unassigned": len(self.unassigned), "canceled": len(self.canceled)}


def build_plan(prob: Problem, sol: Solution, kind: str) -> Plan:
    """由求解结果生成完整计划（站点时刻、路段、费用）。"""
    omap, vmap = prob.order_map(), prob.vehicle_map()
    plan = Plan(kind=kind, unassigned=dict(sol.unassigned))
    for vid, oids in sorted(sol.routes.items()):
        if not oids:
            continue
        v = vmap[vid]
        orders = [omap[i] for i in oids]
        sim = simulate(v, orders, prob)
        if not sim.feasible:  # 求解器保证可行；防御
            raise ValueError(f"infeasible route on {vid}: {sim.reason}")
        vp = VehiclePlan(vehicle_id=vid, depart_depot=sim.depart_depot,
                         return_depot=sim.return_depot)
        prev = prob.depot
        t = sim.depart_depot
        for ss in sim.stops:
            vp.legs.append(Leg(prev, ss.node, t, ss.arrival, ss.leg_km, ss.leg_sec))
            vp.stops.append(StopPlan(ss.order_id, ss.node, ST_PLANNED, ss.arrival,
                                     ss.service_start, ss.service_end,
                                     ss.service_end, ss.wait))
            prev = ss.node
            t = ss.service_end
        vp.legs.append(Leg(prev, prob.depot, t, sim.return_depot, sim.back_km, sim.back_sec))
        vp.km = round(sim.total_km, 3)
        vp.sec = sim.total_sec
        vp.cost = round(v.activation_fee + v.cost_per_km * sim.total_km, 2)
        plan.vehicles[vid] = vp
    return plan


def vehicle_events(prob: Problem, vp: VehiclePlan) -> list[dict]:
    """把一辆车的时间线展开为按时刻排序的事件流（回放用）。"""
    ev: list[dict] = []
    if not vp.legs:
        return ev
    for leg in vp.legs:
        ev.append({"t": leg.depart, "type": "depart", "node": leg.frm,
                   "to": leg.to, "km": leg.km, "vehicle": vp.vehicle_id})
        ev.append({"t": leg.arrive, "type": "arrive", "node": leg.to,
                   "vehicle": vp.vehicle_id})
    for s in vp.stops:
        if s.status in (ST_PLANNED, ST_DONE, ST_SERVING):
            ev.append({"t": s.service_start, "type": "service_start",
                       "node": s.node, "order": s.order_id, "vehicle": vp.vehicle_id})
            ev.append({"t": s.service_end, "type": "service_end",
                       "node": s.node, "order": s.order_id, "vehicle": vp.vehicle_id})
        elif s.status == ST_CANCEL_VISIT:
            ev.append({"t": s.arrival, "type": "cancel_no_unload",
                       "node": s.node, "order": s.order_id, "vehicle": vp.vehicle_id})
    ev.sort(key=lambda e: (e["t"], e["type"]))
    return ev
