"""回放暂停后的改单：取消未开始服务的订单、给未出库车辆加单，然后重规划。

固定不动：已发生的行驶、已完成/进行中的服务、正在前往的下一站（这段行驶仍完成）。
- 取消"当前下一站"：车辆照常开到，不卸货，随即继续；货物占容量直到回库。
- 取消更靠后的站：直接从路线移除（不再前往），已出库车辆的货物仍占容量直到回库。
- 车辆尚未出库：取消即不装车（释放容量），其全部未取消订单参与重新优化。
- 新订单只能分给尚未出库的车辆。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from models import (ASSIGNED, CANCELED, DONE, PENDING, UNASSIGNED,
                    Order, Problem)
from plan import (Leg, Plan, ST_CANCEL_SKIP, ST_CANCEL_VISIT, ST_DONE,
                  ST_PLANNED, ST_SERVING, StopPlan, VehiclePlan)
from solver import Solution, improve, initial_solution


@dataclass
class ReplanResult:
    plan: Plan
    warnings: list[str] = field(default_factory=list)
    rejected_cancels: dict[str, str] = field(default_factory=dict)


def _departed(vp: VehiclePlan, T: int) -> bool:
    return bool(vp.legs) and vp.depart_depot is not None and vp.depart_depot <= T


def _rebuild_departed(prob: Problem, vp: VehiclePlan, T: int,
                      cancel: set[str], warnings: list[str]) -> None:
    """对已出库车辆应用取消，并重算未固定部分的时刻。"""
    tm, dm, depot = prob.time_matrix, prob.dist_matrix, prob.depot
    omap = prob.order_map()
    v = prob.vehicle_map()[vp.vehicle_id]

    # 1) 分类站点
    last_fixed_time = vp.depart_depot
    last_fixed_node = depot
    n = len(vp.stops)
    keep: list[StopPlan] = []
    i = 0
    while i < n:
        s = vp.stops[i]
        o = omap[s.order_id]
        if s.service_end is not None and s.service_end <= T:
            s.status = ST_DONE            # 已完成：固定
            last_fixed_time, last_fixed_node = s.service_end, s.node
            keep.append(s)
        elif s.service_start is not None and s.service_start <= T < s.service_end:
            s.status = ST_SERVING         # 进行中：固定
            last_fixed_time, last_fixed_node = s.service_end, s.node
            keep.append(s)
        elif s.arrival is not None and s.arrival <= T < s.service_start:
            # 已到达等待中：行驶已发生；取消则不卸货、立即离开
            if s.order_id in cancel:
                s.status = ST_CANCEL_VISIT
                s.service_start = s.service_end = None
                s.depart = T
                s.wait = 0
                s.cargo_aboard = True
                last_fixed_time, last_fixed_node = T, s.node
            else:
                last_fixed_time, last_fixed_node = s.service_end, s.node
            keep.append(s)
        else:
            break
        i += 1

    # i 之后的第一站：若车辆正在前往它，则为"当前下一站"（固定到达）
    rest: list[StopPlan] = []
    if i < n:
        nxt = vp.stops[i]
        enroute_to_next = last_fixed_time <= T < (nxt.arrival or 0)
        if nxt.order_id in cancel:
            if enroute_to_next:
                nxt.status = ST_CANCEL_VISIT   # 照常开到，不卸货
                nxt.service_start = nxt.service_end = None
                nxt.depart = nxt.arrival
                nxt.wait = 0
                nxt.cargo_aboard = True
                keep.append(nxt)
                last_fixed_time, last_fixed_node = nxt.arrival, nxt.node
            else:
                nxt.status = ST_CANCEL_SKIP    # 不再前往，货物留车上
                nxt.arrival = nxt.service_start = nxt.service_end = nxt.depart = None
                nxt.cargo_aboard = True
                keep.append(nxt)
        else:
            rest.append(nxt)
        i += 1
    for s in vp.stops[i:]:
        if s.order_id in cancel:
            s.status = ST_CANCEL_SKIP
            s.arrival = s.service_start = s.service_end = s.depart = None
            s.cargo_aboard = True
            keep.append(s)
        else:
            rest.append(s)

    # 2) 从最后一个固定点重算其余保留站点的时刻
    t, node = last_fixed_time, last_fixed_node
    new_stops: list[StopPlan] = [s for s in keep]
    for s in rest:
        o = omap[s.order_id]
        arrival = t + tm[node][s.node]
        svc_start = max(arrival, o.tw_start)
        if svc_start > o.tw_end:
            warnings.append(f"车辆 {v.name} 取消站点后，订单 {o.name} 时窗不可行（保留原计划，请人工核查）")
        svc_end = svc_start + o.service
        s.arrival, s.service_start, s.service_end, s.depart = arrival, svc_start, svc_end, svc_end
        s.wait = svc_start - arrival
        s.status = ST_PLANNED
        new_stops.append(s)
        t, node = svc_end, s.node
    ret = t + tm[node][depot]
    if ret > v.shift_end:
        warnings.append(f"车辆 {v.name} 重算后回库晚于班次结束（请人工核查）")
    vp.stops = new_stops
    vp.return_depot = ret

    # 3) 重建路段（固定段时刻不变，自然与原有一致）
    legs: list[Leg] = []
    t, node = vp.depart_depot, depot
    for s in vp.stops:
        if s.status == ST_CANCEL_SKIP:
            continue
        arr = s.arrival
        legs.append(Leg(node, s.node, t, arr, dm[node][s.node], tm[node][s.node]))
        t, node = s.depart, s.node
    legs.append(Leg(node, depot, t, ret, dm[node][depot], tm[node][depot]))
    vp.legs = legs
    vp.km = round(sum(l.km for l in legs), 3)
    vp.sec = sum(l.sec for l in legs)
    vp.cost = round(v.activation_fee + v.cost_per_km * vp.km, 2)


def replan(prob: Problem, plan: Plan, T: int, cancel_ids: list[str],
           new_orders: list[Order], budget_sec: float = 5.0) -> ReplanResult:
    """在回放时刻 T 暂停：应用取消与加单，返回新计划。"""
    warnings: list[str] = []
    rejected: dict[str, str] = {}
    omap = prob.order_map()
    cancel_set: set[str] = set()

    # 校验取消请求：只能取消"服务尚未开始"的已分配订单（未分配订单也可取消，视为放弃）
    assigned_at: dict[str, tuple[str, StopPlan]] = {}
    for vid, vp in plan.vehicles.items():
        for s in vp.stops:
            assigned_at[s.order_id] = (vid, s)
    for oid in cancel_ids:
        if oid in assigned_at:
            _, s = assigned_at[oid]
            if s.status in (ST_DONE, ST_SERVING) or \
               (s.service_start is not None and s.service_start <= T):
                rejected[oid] = "服务已开始或已完成，不能取消"
                continue
            cancel_set.add(oid)
        elif oid in plan.unassigned or oid in omap:
            cancel_set.add(oid)  # 未分配订单：直接放弃
        else:
            rejected[oid] = "订单不存在"

    for o in new_orders:
        o.status = PENDING
        prob.orders.append(o)
    omap = prob.order_map()
    for oid in cancel_set:
        omap[oid].cancel_time = T

    new_plan = Plan(kind="replan", pause_time=T)
    pool: list[Order] = []
    free_vehicles = []

    for vid in sorted(plan.vehicles):
        vp = plan.vehicles[vid]
        if _departed(vp, T):
            _rebuild_departed(prob, vp, T, cancel_set, warnings)
            new_plan.vehicles[vid] = vp
        else:
            # 未出库：全部未取消订单回池，车辆参与重新优化
            free_vehicles.append(prob.vehicle_map()[vid])
            for s in vp.stops:
                if s.order_id not in cancel_set:
                    pool.append(omap[s.order_id])
    # 从未排过车的车辆也算未出库
    planned_vids = set(plan.vehicles)
    for v in prob.vehicles:
        if v.id not in planned_vids:
            free_vehicles.append(v)
    free_vehicles.sort(key=lambda v: v.id)

    # 未分配（且未取消）的旧订单 + 新订单入池
    for oid in plan.unassigned:
        if oid not in cancel_set:
            pool.append(omap[oid])
    pool.extend(new_orders)
    for o in pool:
        o.status = PENDING
        o.reason = ""

    if pool and free_vehicles:
        sub = initial_solution(prob, pool, free_vehicles)
        sub = improve(prob, sub, budget_sec=budget_sec)
        from plan import build_plan
        built = build_plan(prob, sub, "replan")
        new_plan.vehicles.update(built.vehicles)
        new_plan.unassigned = dict(sub.unassigned)
    elif pool and not free_vehicles:
        for o in pool:
            new_plan.unassigned[o.id] = "所有车辆均已出库，无法接收新订单" \
                if o in new_orders else "无可行插入（启发式未找到，不代表无解）"

    new_plan.canceled = sorted(cancel_set)
    if rejected:
        warnings.append("部分取消被拒绝：" + "; ".join(f"{k}: {v}" for k, v in rejected.items()))
    new_plan.note = "；".join(warnings)
    return ReplanResult(new_plan, warnings, rejected)
