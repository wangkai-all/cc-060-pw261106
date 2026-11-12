"""端到端测试：可复现性、样例特征、回放取消/加单语义、费用与独立校验。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store
import validate
from models import Order, hhmm
from plan import (ST_CANCEL_SKIP, ST_CANCEL_VISIT, ST_DONE, ST_PLANNED,
                  ST_SERVING, build_plan)
from replan import replan
from sample_data import load_sample
from solver import evaluate, improve, initial_solution


# ---------------------------------------------------------------- 工具

def make_db(tmp_path, prob):
    db = str(tmp_path / "t.db")
    conn = store.connect(db)
    store.reset(conn)
    store.save_problem(conn, prob, "test")
    return db, conn


def solve(prob):
    sol = initial_solution(prob)
    return sol, improve(prob, sol)


# ---------------------------------------------------------------- 初始解

def test_initial_deterministic():
    p1, p2 = load_sample("main"), load_sample("main")
    s1 = initial_solution(p1)
    s2 = initial_solution(p2)
    assert s1.routes == s2.routes
    assert s1.unassigned == s2.unassigned


def test_improve_lexicographic():
    prob = load_sample("main")
    sol0, sol1 = solve(prob)
    m0 = evaluate(prob, sol0)
    m1 = evaluate(prob, sol1)
    # 字典序：送达数(取负) -> 车辆数 -> 总行驶时间，改进解不更差
    assert m1 <= m0
    assert -m1[0] >= -m0[0]          # 送达数不减少
    assert m1[1] <= m0[1]            # 车辆数不增加


def test_main_sample_features():
    prob = load_sample("main")
    sol0, sol1 = solve(prob)
    assigned = set().union(*[set(v) for v in sol1.routes.values()]) if sol1.routes else set()
    # A09 超所有车辆容量 -> 未分配且为容量原因
    assert "A09" in sol1.unassigned
    assert "载重" in sol1.unassigned["A09"] or "容积" in sol1.unassigned["A09"]
    # 其余订单启发式应能送达
    assert "A09" not in assigned


# ---------------------------------------------------------------- 样例特征

def test_waiting_sample_has_wait():
    prob = load_sample("waiting")
    sol0, sol1 = solve(prob)
    plan = build_plan(prob, sol1, "improved")
    waits = [s.wait for vp in plan.vehicles.values() for s in vp.stops]
    assert max(waits) > 3600          # 早到等待超过 1 小时


def test_capacity_sample_unassigned():
    prob = load_sample("capacity")
    sol0, sol1 = solve(prob)
    assert "C02" in sol1.unassigned and "载重" in sol1.unassigned["C02"] or \
           "载重" in sol1.unassigned.get("C02", "") or "容积" in sol1.unassigned.get("C02", "")
    assert "C03" in sol1.unassigned
    assert "C01" not in sol1.unassigned   # 正常件应送达


def test_asymmetric_matrix_direction():
    prob = load_sample("asymmetric")
    assert prob.time_matrix[0][1] == 600 and prob.time_matrix[1][0] == 3000
    assert prob.dist_matrix[0][1] == 12.0 and prob.dist_matrix[1][0] == 30.0
    sol0, sol1 = solve(prob)
    plan = build_plan(prob, sol1, "improved")
    # 所有车辆的路段行驶时间必须按方向取矩阵值
    for vp in plan.vehicles.values():
        for leg in vp.legs:
            assert leg.sec == prob.time_matrix[leg.frm][leg.to]
            assert leg.km == prob.dist_matrix[leg.frm][leg.to]
    # 回库段应是 1->0 或 2->0 的方向值
    last = list(plan.vehicles.values())[0].legs[-1]
    assert last.to == 0
    assert last.sec == prob.time_matrix[last.frm][0]


# ---------------------------------------------------------------- 回放：取消

def _first_departed_vehicle(plan, T):
    for vid, vp in plan.vehicles.items():
        if vp.legs and vp.depart_depot <= T:
            return vid, vp
    return None, None


def test_cancel_next_stop_keeps_leg_no_unload():
    prob = load_sample("midday")
    sol0, sol1 = solve(prob)
    plan = build_plan(prob, sol1, "improved")
    # 找一辆已出库车的第一段行驶，在行驶途中暂停
    vid, vp = _first_departed_vehicle(plan, hhmm(9, 30))
    assert vid is not None
    leg0 = vp.legs[0]
    T = leg0.depart + 1                     # 正在前往第一站
    next_stop = vp.stops[0]
    res = replan(prob, plan, T, [next_stop.order_id], [])
    vp2 = res.plan.vehicles[vid]
    s0 = vp2.stops[0]
    assert s0.status == ST_CANCEL_VISIT     # 仍完成该段行驶
    assert s0.arrival == leg0.arrive        # 到达时刻不变
    assert s0.service_start is None and s0.service_end is None   # 不卸货
    assert s0.cargo_aboard                  # 货物占容量直到回库
    # 该段行驶仍在路段中
    assert any(l.frm == leg0.frm and l.to == leg0.to and l.depart == leg0.depart
               for l in vp2.legs)


def test_cancel_inservice_rejected():
    prob = load_sample("midday")
    sol0, sol1 = solve(prob)
    plan = build_plan(prob, sol1, "improved")
    vid, vp = _first_departed_vehicle(plan, hhmm(9, 30))
    s0 = vp.stops[0]
    T = s0.service_start + 1                # 服务进行中
    res = replan(prob, plan, T, [s0.order_id], [])
    assert s0.order_id in res.rejected_cancels
    # 服务状态保持
    assert res.plan.vehicles[vid].stops[0].status in (ST_SERVING, ST_DONE)


def test_cancel_later_stop_skipped():
    prob = load_sample("main")
    sol0, sol1 = solve(prob)
    plan = build_plan(prob, sol1, "improved")
    # 找一条有 >=2 站的已出库路线，取消第二站
    target = None
    for vid, vp in plan.vehicles.items():
        if len(vp.stops) >= 2 and vp.depart_depot <= hhmm(9, 0):
            target = (vid, vp)
            break
    if target is None:
        pytest.skip("无多站路线")
    vid, vp = target
    second = vp.stops[1]
    T = vp.legs[0].depart + 1               # 正在前往第一站，第二站尚未前往
    res = replan(prob, plan, T, [second.order_id], [])
    vp2 = res.plan.vehicles[vid]
    canceled_stop = [s for s in vp2.stops if s.order_id == second.order_id][0]
    assert canceled_stop.status == ST_CANCEL_SKIP
    assert canceled_stop.arrival is None    # 不再前往
    assert canceled_stop.cargo_aboard       # 货物仍占容量直到回库


def test_new_order_only_to_not_departed():
    prob = load_sample("midday")
    sol0, sol1 = solve(prob)
    plan = build_plan(prob, sol1, "improved")
    T = hhmm(9, 30)                          # V1 已出库，V2 班次 11 点尚未出库
    new = Order("N001", "临时加单", 3, 100, 1.0, 600, hhmm(13), hhmm(16))
    res = replan(prob, plan, T, [], [new])
    # 新单只能落在 V2（11 点出库）或保持未分配，绝不能落在已出库的 V1
    v1_ids = {s.order_id for s in res.plan.vehicles.get("V1", []).stops}
    assert "N001" not in v1_ids
    placed = any(s.order_id == "N001" for vid, vp in res.plan.vehicles.items()
                 for s in vp.stops if vid != "V1")
    assert placed or "N001" in res.plan.unassigned


def test_new_order_all_departed_rejected():
    prob = load_sample("waiting")            # 只有一辆车，8 点出库
    sol0, sol1 = solve(prob)
    plan = build_plan(prob, sol1, "improved")
    T = hhmm(11, 0)                          # 唯一车辆早已出库
    new = Order("N009", "临时加单", 2, 50, 0.5, 300, hhmm(12), hhmm(13))
    res = replan(prob, plan, T, [], [new])
    assert "N009" in res.plan.unassigned
    assert "出库" in res.plan.unassigned["N009"]


# ---------------------------------------------------------------- 费用

def test_cost_activation_plus_per_km():
    prob = load_sample("main")
    sol0, sol1 = solve(prob)
    plan = build_plan(prob, sol1, "improved")
    vmap = prob.vehicle_map()
    for vid, vp in plan.vehicles.items():
        v = vmap[vid]
        km = sum(l.km for l in vp.legs)
        expect = round(v.activation_fee + v.cost_per_km * km, 2)
        assert abs(vp.cost - expect) < 0.01
        assert abs(vp.km - round(km, 3)) < 0.01


def test_fixed_prefix_preserved_after_replan():
    """T 之前已发生的行驶、已完成/进行中的服务、正在前往的下一站都固定。"""
    prob = load_sample("main")
    sol0, sol1 = solve(prob)
    plan = build_plan(prob, sol1, "improved")
    # 选一辆已出库车，在服务进行中暂停
    vid, vp = _first_departed_vehicle(plan, hhmm(9, 30))
    s0 = vp.stops[0]
    T = s0.service_start + 1
    # 取消一个更靠后的站（若有）
    later = [s.order_id for s in vp.stops[1:]]
    res = replan(prob, plan, T, later[:1], [])
    vp2 = res.plan.vehicles[vid]
    # T 之前出发的路段逐条不变
    old_legs = [l for l in vp.legs if l.depart <= T]
    new_legs = [l for l in vp2.legs if l.depart <= T]
    assert [(l.frm, l.to, l.depart, l.arrive) for l in old_legs] == \
           [(l.frm, l.to, l.depart, l.arrive) for l in new_legs]
    # 已完成/进行中的站点时刻不变
    for s_old in vp.stops:
        if s_old.service_start is not None and s_old.service_start <= T:
            s_new = [s for s in vp2.stops if s.order_id == s_old.order_id][0]
            assert s_new.status in (ST_DONE, ST_SERVING)
            assert (s_new.arrival, s_new.service_start, s_new.service_end) == \
                   (s_old.arrival, s_old.service_start, s_old.service_end)


# ---------------------------------------------------------------- 独立校验

def test_validator_passes_all_samples(tmp_path):
    for name in ("main", "waiting", "capacity", "asymmetric", "midday"):
        prob = load_sample(name)
        db, conn = make_db(tmp_path, prob)
        sol0, sol1 = solve(prob)
        for sol, kind in ((sol0, "initial"), (sol1, "improved")):
            plan = build_plan(prob, sol, kind)
            pid = store.save_plan(conn, plan, prob)
            rep = validate.validate_plan(db, pid)
            fails = [c for c in rep.checks if not c["ok"]]
            assert rep.ok, f"{name}/{kind}: " + "; ".join(
                f"{c['check']}:{c['detail']}" for c in fails)
        conn.close()


def test_validator_after_replan(tmp_path):
    prob = load_sample("midday")
    db, conn = make_db(tmp_path, prob)
    sol0, sol1 = solve(prob)
    plan = build_plan(prob, sol1, "improved")
    pid0 = store.save_plan(conn, plan, prob)
    # 暂停 -> 取消 V1 当前下一站 -> 给 V2 加单
    vid, vp = _first_departed_vehicle(plan, hhmm(9, 30))
    leg0 = vp.legs[0]
    T = leg0.depart + 1
    cancel_oid = vp.stops[0].order_id
    new = Order("N100", "临时加单", 4, 100, 1.0, 600, hhmm(14), hhmm(17))
    res = replan(prob, plan, T, [cancel_oid], [new])
    store.save_orders(conn, prob)          # 新订单入库（API 流程中由接口层保存）
    pid1 = store.save_plan(conn, res.plan, prob)
    rep = validate.validate_plan(db, pid1)
    fails = [c for c in rep.checks if not c["ok"]]
    assert rep.ok, "; ".join(f"{c['check']}:{c['detail']}" for c in fails)
    conn.close()
