#!/usr/bin/env python3
"""端到端演示：排车 -> 打印每车时间线 -> 独立校验 -> 暂停后取消/加单 -> 再校验。

用法: python demo.py [样例名]   （默认 main）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store
import validate
from models import Order, fmt_time, hhmm
from plan import build_plan
from replan import replan
from sample_data import SAMPLES, load_sample
from solver import evaluate, improve, initial_solution

DB = "/tmp/vrp_demo.db"


def show_plan(prob, plan, title):
    print(f"\n----- {title} -----")
    m = plan.metrics(prob)
    print(f"送达 {m['delivered']} 单 | 出车 {m['vehicles']} 辆 | "
          f"总行驶 {m['travel_sec']//60} 分钟 | {m['distance_km']} km | 费用 ¥{m['cost']}")
    nmap = {n.id: n.name for n in prob.nodes}
    omap = prob.order_map()
    for vid, vp in sorted(plan.vehicles.items()):
        if not vp.legs:
            continue
        print(f"\n  {vid}  出库 {fmt_time(vp.depart_depot)} -> 回库 {fmt_time(vp.return_depot)}"
              f"  ({vp.km} km, ¥{vp.cost})")
        for s in vp.stops:
            o = omap[s.order_id]
            if s.status == "canceled_skip":
                print(f"    ✗ {o.name} 已取消（不再前往，货物留车至回库）")
                continue
            if s.status == "canceled_visit":
                print(f"    ✗ {fmt_time(s.arrival)} 到 {nmap[s.node]}｜{o.name} 已取消，不卸货")
                continue
            wait = f"  早到等待 {s.wait//60} 分钟" if s.wait else ""
            print(f"    {fmt_time(s.arrival)} 到 {nmap[s.node]}｜{o.name}"
                  f"  {fmt_time(s.service_start)}~{fmt_time(s.service_end)} 服务{wait}")
    if plan.unassigned:
        print("\n  未分配订单：")
        for oid, reason in sorted(plan.unassigned.items()):
            print(f"    - {omap[oid].name}: {reason}")


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "main"
    prob = load_sample(name)

    sol0 = initial_solution(prob)
    plan0 = build_plan(prob, sol0, "initial")
    sol1 = improve(prob, sol0)
    plan1 = build_plan(prob, sol1, "improved")

    print(f"===== 样例：{SAMPLES[name][0]} =====")
    show_plan(prob, plan0, "初始方案（可复现）")
    show_plan(prob, plan1, "改进方案（先比送达数，再比车辆数，再比总行驶时间）")
    m0, m1 = plan0.metrics(prob), plan1.metrics(prob)
    better = []
    if m1["delivered"] != m0["delivered"]:
        better.append(f"送达 {m0['delivered']}→{m1['delivered']}")
    if m1["vehicles"] != m0["vehicles"]:
        better.append(f"车辆 {m0['vehicles']}→{m1['vehicles']}")
    if m1["travel_sec"] != m0["travel_sec"]:
        better.append(f"行驶 {m0['travel_sec']//60}→{m1['travel_sec']//60} 分钟")
    print("\n改进效果: " + ("，".join(better) if better else "与初始方案相同"))

    # 入库 + 独立校验
    if os.path.exists(DB):
        os.remove(DB)
    conn = store.connect(DB)
    store.save_problem(conn, prob, name)
    pid0 = store.save_plan(conn, plan0, prob)
    pid1 = store.save_plan(conn, plan1, prob)
    conn.close()
    for pid in (pid0, pid1):
        rep = validate.validate_plan(DB, pid)
        print(f"\n独立校验 计划#{pid}({('初始','改进')[pid==pid1]}): "
              + ("全部通过 ✓" if rep.ok else "存在失败 ✗"))
        for c in rep.checks:
            if not c["ok"]:
                print(f"   ✗ {c['check']} — {c['detail']}")

    # ---- 回放暂停：取消当前下一站 + 中途加单 ----
    print("\n===== 回放暂停改单 =====")
    T = None
    cancel_oid = None
    for vid, vp in sorted(plan1.vehicles.items()):
        if vp.legs and vp.depart_depot <= hhmm(9, 30) and vp.stops:
            leg0 = vp.legs[0]
            T = leg0.depart + 60
            cancel_oid = vp.stops[0].order_id
            break
    if T is None:
        print("该样例没有 09:30 前出库的车辆，跳过改单演示")
        return
    omap = prob.order_map()
    print(f"回放暂停于 {fmt_time(T)}：车辆正在前往 {omap[cancel_oid].name}，取消该单；")
    late_node = prob.orders[-1].node
    new = Order("N900", "临时加单", late_node, 80, 0.6, 600, hhmm(13), hhmm(17))
    print(f"同时加单：{new.name}（{new.weight}kg，时窗 {fmt_time(new.tw_start)}~{fmt_time(new.tw_end)}）")
    res = replan(prob, plan1, T, [cancel_oid], [new])
    for w in res.warnings:
        print("  警告:", w)
    show_plan(prob, res.plan, f"重规划方案（暂停于 {fmt_time(T)}）")
    conn = store.connect(DB)
    store.save_orders(conn, prob)
    pid2 = store.save_plan(conn, res.plan, prob)
    conn.close()
    rep = validate.validate_plan(DB, pid2)
    print(f"\n独立校验 计划#{pid2}(重规划): " + ("全部通过 ✓" if rep.ok else "存在失败 ✗"))
    for c in rep.checks:
        if not c["ok"]:
            print(f"   ✗ {c['check']} — {c['detail']}")


if __name__ == "__main__":
    main()
