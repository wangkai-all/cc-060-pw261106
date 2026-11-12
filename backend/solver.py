"""求解器：可复现的初始解 + 字典序目标改进（送达订单数 -> 车辆数 -> 总行驶时间）。

全程确定性：固定遍历顺序、固定平局裁决（按 id 字典序），无随机数。
启发式找不到方案时只报告"未分配及原因"，不宣称问题无解。
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field

from models import ASSIGNED, PENDING, UNASSIGNED, Order, Problem, Vehicle
from timeline import capacity_ok, route_feasible, simulate


@dataclass
class Solution:
    routes: dict[str, list[str]] = field(default_factory=dict)   # vehicle_id -> [order_id]
    unassigned: dict[str, str] = field(default_factory=dict)     # order_id -> reason

    def copy(self) -> "Solution":
        return Solution({k: list(v) for k, v in self.routes.items()}, dict(self.unassigned))


def _orders_of(sol_routes: dict[str, list[str]]) -> set[str]:
    out: set[str] = set()
    for v in sol_routes.values():
        out.update(v)
    return out


def evaluate(prob: Problem, sol: Solution) -> tuple[int, int, int]:
    """目标元组：(送达数取负, 车辆数, 总行驶秒) —— 越小越好。"""
    omap = prob.order_map()
    vmap = prob.vehicle_map()
    delivered = 0
    used = 0
    travel = 0
    for vid, oids in sol.routes.items():
        if not oids:
            continue
        used += 1
        delivered += len(oids)
        sim = simulate(vmap[vid], [omap[i] for i in oids], prob)
        travel += sim.total_sec
    return (-delivered, used, travel)


def _metrics(prob: Problem, sol: Solution) -> dict:
    omap, vmap = prob.order_map(), prob.vehicle_map()
    delivered = used = travel = 0
    km = 0.0
    cost = 0.0
    for vid, oids in sol.routes.items():
        if not oids:
            continue
        v = vmap[vid]
        used += 1
        delivered += len(oids)
        sim = simulate(v, [omap[i] for i in oids], prob)
        travel += sim.total_sec
        km += sim.total_km
        cost += v.activation_fee + v.cost_per_km * sim.total_km
    return {"delivered": delivered, "vehicles": used, "travel_sec": travel,
            "distance_km": round(km, 3), "cost": round(cost, 2)}


# ---------------------------------------------------------------- 初始解

def initial_solution(prob: Problem, orders: list[Order] | None = None,
                     vehicles: list[Vehicle] | None = None) -> Solution:
    """确定性插入启发式：按 (时窗止, 时窗起, id) 处理订单；
    每单尝试插入所有已开路线的所有位置（取新增行驶时间最小者），
    不行则启用一辆新车（按 启用费+每公里费、id 排序选第一辆可行的）。"""
    omap = prob.order_map()
    cand_vehicles = vehicles if vehicles is not None else list(prob.vehicles)
    if orders is not None:
        todo = sorted(orders, key=lambda o: (o.tw_end, o.tw_start, o.id))
    else:
        todo = sorted((o for o in prob.orders if o.status == PENDING),
                      key=lambda o: (o.tw_end, o.tw_start, o.id))
    free = sorted(cand_vehicles, key=lambda v: (v.activation_fee, v.cost_per_km, v.id))
    sol = Solution()

    def best_insert(oid: str, routes: dict[str, list[str]]):
        """在现有路线中找 (增量行驶秒, vehicle_id, pos) 最优可行插入。"""
        best = None
        for vid in sorted(routes):
            v = prob.vehicle_map()[vid]
            cur = [omap[i] for i in routes[vid]]
            base = simulate(v, cur, prob).total_sec if cur else 0
            for pos in range(len(routes[vid]) + 1):
                trial = routes[vid][:pos] + [oid] + routes[vid][pos:]
                orders_t = [omap[i] for i in trial]
                ok, _ = capacity_ok(v, orders_t)
                if not ok:
                    continue
                sim = simulate(v, orders_t, prob)
                if not sim.feasible:
                    continue
                key = (sim.total_sec - base, vid, pos)
                if best is None or key < best[0]:
                    best = (key, vid, pos)
        return best

    for o in todo:
        # 单订单本身就超过所有可用车辆容量 -> 直接给容量原因
        fits_some = any(o.weight <= v.cap_weight + 1e-9 and o.volume <= v.cap_volume + 1e-9
                        for v in cand_vehicles)
        if not fits_some:
            sol.unassigned[o.id] = "超过所有车辆载重/容积上限"
            continue
        hit = best_insert(o.id, sol.routes)
        if hit is not None:
            _, vid, pos = hit
            sol.routes[vid].insert(pos, o.id)
            continue
        # 启用新车
        opened = False
        for v in free:
            ok, _ = capacity_ok(v, [o])
            if not ok:
                continue
            sim = simulate(v, [o], prob)
            if sim.feasible:
                sol.routes[v.id] = [o.id]
                free.remove(v)
                opened = True
                break
        if not opened:
            sol.unassigned[o.id] = "时窗/班次/容量约束下无可行插入（启发式未找到，不代表无解）"
    return sol


# ---------------------------------------------------------------- 改进

def _try_best_insertion(prob: Problem, routes: dict[str, list[str]], oid: str,
                        allow_vehicle_ids: set[str] | None = None):
    omap = prob.order_map()
    best = None
    for vid in sorted(routes):
        if allow_vehicle_ids is not None and vid not in allow_vehicle_ids:
            continue
        if not routes[vid]:
            continue
        v = prob.vehicle_map()[vid]
        cur = [omap[i] for i in routes[vid]]
        base = simulate(v, cur, prob).total_sec
        for pos in range(len(routes[vid]) + 1):
            trial_ids = routes[vid][:pos] + [oid] + routes[vid][pos:]
            orders_t = [omap[i] for i in trial_ids]
            ok, _ = capacity_ok(v, orders_t)
            if not ok:
                continue
            sim = simulate(v, orders_t, prob)
            if not sim.feasible:
                continue
            key = (sim.total_sec - base, vid, pos)
            if best is None or key < best[0]:
                best = (key, vid, pos)
    return best


def improve(prob: Problem, sol: Solution, budget_sec: float = 5.0) -> Solution:
    """字典序改进：先尽量多送达，再减少车辆，再压缩总行驶时间。确定性。"""
    omap = prob.order_map()
    vmap = prob.vehicle_map()
    best = sol.copy()
    t0 = _time.monotonic()

    def expired() -> bool:
        return _time.monotonic() - t0 > budget_sec

    # 阶段A：把未分配订单插回去（提升送达数）
    changed = True
    while changed and not expired():
        changed = False
        for oid in sorted(list(best.unassigned)):
            hit = _try_best_insertion(prob, best.routes, oid)
            if hit is not None:
                _, vid, pos = hit
                best.routes[vid].insert(pos, oid)
                del best.unassigned[oid]
                changed = True

    # 阶段B：减车 —— 尝试把某辆车的全部订单挪进其它路线
    improved = True
    while improved and not expired():
        improved = False
        used = sorted((vid for vid, r in best.routes.items() if r),
                      key=lambda v: (len(best.routes[v]), v))
        for vid in used:
            oids = list(best.routes[vid])
            trial_routes = {k: list(v) for k, v in best.routes.items()}
            trial_routes[vid] = []
            ok_all = True
            for oid in oids:  # 按原顺序逐个最佳插入
                hit = _try_best_insertion(prob, trial_routes, oid)
                if hit is None:
                    ok_all = False
                    break
                _, tvid, pos = hit
                trial_routes[tvid].insert(pos, oid)
            if ok_all:
                best.routes = trial_routes
                improved = True
                break

    # 阶段C：压缩总行驶时间（不改变已送达集合）—— relocate / swap / 2-opt
    def total_travel(routes: dict[str, list[str]]) -> int:
        tot = 0
        for vid, oids in routes.items():
            if oids:
                tot += simulate(vmap[vid], [omap[i] for i in oids], prob).total_sec
        return tot

    cur_travel = total_travel(best.routes)
    moved = True
    while moved and not expired():
        moved = False
        best_move = None  # (new_travel, kind, payload)
        used_vids = sorted(v for v, r in best.routes.items() if r)

        # relocate：把订单 i 从车 a 移到车 b 的位置 p（含同车重排）
        for a in used_vids:
            ra = best.routes[a]
            for i, oid in enumerate(ra):
                rest = ra[:i] + ra[i + 1:]
                for b in used_vids:
                    for p in range(len(best.routes[b]) + 1):
                        if b == a and (p == i or p == i + 1):
                            continue
                        if b == a:
                            nb_ids = rest[:p] + [oid] + rest[p:]
                            trial = {**best.routes, a: nb_ids}
                        else:
                            nb_ids = best.routes[b][:p] + [oid] + best.routes[b][p:]
                            trial = {**best.routes, a: rest, b: nb_ids}
                        va, vb = vmap[a], vmap[b]
                        oa = [omap[x] for x in trial[a]]
                        ob = [omap[x] for x in trial[b]]
                        if oa:
                            ok, _ = capacity_ok(va, oa)
                            if not ok:
                                continue
                            sa = simulate(va, oa, prob)
                            if not sa.feasible:
                                continue
                        if b != a and ob:
                            ok, _ = capacity_ok(vb, ob)
                            if not ok:
                                continue
                            sb = simulate(vb, ob, prob)
                            if not sb.feasible:
                                continue
                        t = total_travel(trial)
                        if t < cur_travel and (best_move is None or t < best_move[0]):
                            best_move = (t, "relocate", trial)

        # swap：车 a 的第 i 单 与 车 b 的第 j 单互换（a<b）
        for x in range(len(used_vids)):
            for y in range(x + 1, len(used_vids)):
                a, b = used_vids[x], used_vids[y]
                ra, rb = best.routes[a], best.routes[b]
                for i in range(len(ra)):
                    for j in range(len(rb)):
                        na = ra[:i] + [rb[j]] + ra[i + 1:]
                        nb = rb[:j] + [ra[i]] + rb[j + 1:]
                        oa = [omap[z] for z in na]
                        ob = [omap[z] for z in nb]
                        ok, _ = capacity_ok(vmap[a], oa)
                        if not ok:
                            continue
                        ok, _ = capacity_ok(vmap[b], ob)
                        if not ok:
                            continue
                        sa = simulate(vmap[a], oa, prob)
                        if not sa.feasible:
                            continue
                        sb = simulate(vmap[b], ob, prob)
                        if not sb.feasible:
                            continue
                        trial = {**best.routes, a: na, b: nb}
                        t = total_travel(trial)
                        if t < cur_travel and (best_move is None or t < best_move[0]):
                            best_move = (t, "swap", trial)

        # 2-opt：单车内部反转一段
        for a in used_vids:
            ra = best.routes[a]
            n = len(ra)
            for i in range(n - 1):
                for j in range(i + 2, n + 1):
                    na = ra[:i] + ra[i:j][::-1] + ra[j:]
                    if na == ra:
                        continue
                    oa = [omap[z] for z in na]
                    sa = simulate(vmap[a], oa, prob)
                    if not sa.feasible:
                        continue
                    trial = {**best.routes, a: na}
                    t = total_travel(trial)
                    if t < cur_travel and (best_move is None or t < best_move[0]):
                        best_move = (t, "2opt", trial)

        if best_move is not None:
            cur_travel, _, best.routes = best_move
            moved = True

    return best
