#!/usr/bin/env python3
"""独立校验程序：直接读取 SQLite 数据库，核对某个计划的
  1) 访问次数  2) 班次起止  3) 承诺时窗  4) 装载容量  5) 费用里程  6) 行驶一致性

不导入任何求解/排程代码，全部从矩阵与计划表重新计算。
用法: python validate.py --db ../data/app.db --plan latest|ID|all [-v]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys

EPS_T = 0.01   # 费用/里程容差


def _rows(conn, sql, args=()):
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, args).fetchall()


class Report:
    def __init__(self):
        self.checks: list[dict] = []

    def add(self, check: str, ok: bool, detail: str = ""):
        self.checks.append({"check": check, "ok": bool(ok), "detail": detail})

    @property
    def ok(self) -> bool:
        return all(c["ok"] for c in self.checks)


def validate_plan(db_path: str, plan_id: int) -> Report:
    rep = Report()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    plan = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if plan is None:
        rep.add("计划存在", False, f"plan {plan_id} 不存在")
        return rep
    rep.add("计划存在", True, f"plan {plan_id} kind={plan['kind']}")

    vehicles = {r["id"]: r for r in _rows(conn, "SELECT * FROM vehicles")}
    orders = {r["id"]: r for r in _rows(conn, "SELECT * FROM orders")}
    tmat = {(r["src"], r["dst"]): r["sec"] for r in _rows(conn, "SELECT * FROM matrix_time")}
    dmat = {(r["src"], r["dst"]): r["km"] for r in _rows(conn, "SELECT * FROM matrix_dist")}
    depot_row = conn.execute("SELECT id FROM nodes WHERE is_depot=1").fetchone()
    depot = depot_row["id"] if depot_row else 0

    stops = _rows(conn, "SELECT * FROM stops WHERE plan_id=? ORDER BY vehicle_id, seq", (plan_id,))
    legs = _rows(conn, "SELECT * FROM legs WHERE plan_id=? ORDER BY vehicle_id, seq", (plan_id,))
    unassigned = {r["order_id"]: r["reason"]
                  for r in _rows(conn, "SELECT * FROM unassigned WHERE plan_id=?", (plan_id,))}
    canceled = {r["order_id"] for r in _rows(conn, "SELECT * FROM canceled WHERE plan_id=?", (plan_id,))}

    by_veh_stops: dict[str, list] = {}
    for s in stops:
        by_veh_stops.setdefault(s["vehicle_id"], []).append(s)
    by_veh_legs: dict[str, list] = {}
    for l in legs:
        by_veh_legs.setdefault(l["vehicle_id"], []).append(l)

    # ---------- 1) 访问次数：每单至多服务一次；取消单不得有服务 ----------
    serviced = [s["order_id"] for s in stops if s["svc_start"] is not None]
    dup = sorted({o for o in serviced if serviced.count(o) > 1})
    rep.add("访问次数·每单至多服务一次", not dup,
            f"重复服务: {dup}" if dup else f"共服务 {len(serviced)} 单，无重复")
    canceled_with_service = sorted(o for o in canceled
                                   if any(s["order_id"] == o and s["svc_start"] is not None
                                          for s in stops))
    rep.add("访问次数·取消订单无服务", not canceled_with_service,
            f"取消单仍被服务: {canceled_with_service}" if canceled_with_service else "通过")
    no_unload = [s for s in stops if s["status"] == "canceled_visit"]
    bad_no_unload = [s["order_id"] for s in no_unload if s["svc_start"] is not None]
    rep.add("访问次数·取消下一站不卸货", not bad_no_unload,
            f"异常: {bad_no_unload}" if bad_no_unload else f"{len(no_unload)} 站到点未卸货")

    # ---------- 2) 班次 ----------
    for vid, vlegs in sorted(by_veh_legs.items()):
        v = vehicles[vid]
        first_depart = min(l["depart"] for l in vlegs)
        last_arrive = max(l["arrive"] for l in vlegs)
        ok = first_depart >= v["shift_start"] and last_arrive <= v["shift_end"]
        rep.add(f"班次·{vid} {v['name']}", ok,
                f"出库 {first_depart}s(≥{v['shift_start']}) 回库 {last_arrive}s(≤{v['shift_end']})")

    # ---------- 3) 时窗与服务时长 ----------
    tw_bad, svc_bad, wait_bad = [], [], []
    for s in stops:
        if s["svc_start"] is None:
            continue
        o = orders[s["order_id"]]
        expect_start = max(s["arrival"], o["tw_start"])
        if s["svc_start"] != expect_start or s["svc_start"] > o["tw_end"]:
            tw_bad.append(f"{s['order_id']}(开始{s['svc_start']} 窗{o['tw_start']}-{o['tw_end']})")
        if s["svc_end"] != s["svc_start"] + o["service"]:
            svc_bad.append(s["order_id"])
        if s["wait"] != s["svc_start"] - s["arrival"] or s["wait"] < 0:
            wait_bad.append(s["order_id"])
    rep.add("时窗·服务开始不晚于时窗止", not tw_bad, "; ".join(tw_bad) if tw_bad else "通过")
    rep.add("时窗·服务时长正确", not svc_bad, "; ".join(svc_bad) if svc_bad else "通过")
    rep.add("时窗·早到等待时长一致", not wait_bad, "; ".join(wait_bad) if wait_bad else "通过")

    # ---------- 4) 装载（单趟：出库时全部在车上；取消货物占容量直到回库） ----------
    for vid, vstops in sorted(by_veh_stops.items()):
        v = vehicles[vid]
        aboard = [s for s in vstops
                  if s["svc_start"] is not None or s["cargo_aboard"]]
        w = sum(orders[s["order_id"]]["weight"] for s in aboard)
        vol = sum(orders[s["order_id"]]["volume"] for s in aboard)
        ok = w <= v["cap_w"] + 1e-9 and vol <= v["cap_v"] + 1e-9
        rep.add(f"装载·{vid} {v['name']}", ok,
                f"{w:.1f}kg/{v['cap_w']:.1f}kg, {vol:.2f}m³/{v['cap_v']:.2f}m³"
                f"（含取消未卸货 {sum(1 for s in aboard if s['cargo_aboard'])} 单）")

    # ---------- 5) 费用与里程 ----------
    metrics = json.loads(plan["metrics"])
    total_cost = 0.0
    total_km = 0.0
    total_sec = 0
    used = 0
    cost_bad = []
    for vid, vlegs in sorted(by_veh_legs.items()):
        v = vehicles[vid]
        km = sum(l["km"] for l in vlegs)
        sec = sum(l["sec"] for l in vlegs)
        cost = v["fee"] + v["per_km"] * km
        total_cost += cost
        total_km += km
        total_sec += sec
        used += 1
    if abs(total_cost - metrics.get("cost", -1)) > EPS_T:
        cost_bad.append(f"费用 库{metrics.get('cost')} ≠ 算{round(total_cost, 2)}")
    if abs(total_km - metrics.get("distance_km", -1)) > EPS_T:
        cost_bad.append(f"里程 库{metrics.get('distance_km')} ≠ 算{round(total_km, 3)}")
    if total_sec != metrics.get("travel_sec", -1):
        cost_bad.append(f"行驶时间 库{metrics.get('travel_sec')} ≠ 算{total_sec}")
    if used != metrics.get("vehicles", -1):
        cost_bad.append(f"车辆数 库{metrics.get('vehicles')} ≠ 算{used}")
    delivered = len({s["order_id"] for s in stops if s["svc_start"] is not None})
    if delivered != metrics.get("delivered", -1):
        cost_bad.append(f"送达数 库{metrics.get('delivered')} ≠ 算{delivered}")
    rep.add("费用·启用费+每公里×实际里程", not cost_bad,
            "; ".join(cost_bad) if cost_bad
            else f"总费用 {round(total_cost, 2)}，里程 {round(total_km, 3)}km，{used} 车")

    # ---------- 6) 行驶一致性（对照矩阵） ----------
    mat_bad, chain_bad = [], []
    for vid, vlegs in sorted(by_veh_legs.items()):
        for k, l in enumerate(vlegs):
            if tmat.get((l["src"], l["dst"])) != l["sec"] or \
               abs(dmat.get((l["src"], l["dst"]), -1) - l["km"]) > EPS_T:
                mat_bad.append(f"{vid}#段{k}")
            if k == 0 and l["src"] != depot:
                chain_bad.append(f"{vid} 首段未从仓库出发")
            if k == len(vlegs) - 1 and l["dst"] != depot:
                chain_bad.append(f"{vid} 末段未回仓库")
            if k > 0:
                prev = vlegs[k - 1]
                if prev["dst"] != l["src"] or l["depart"] < prev["arrive"]:
                    chain_bad.append(f"{vid}#段{k} 衔接异常")
    rep.add("行驶·路段与矩阵一致", not mat_bad, "; ".join(mat_bad) if mat_bad else "通过")
    rep.add("行驶·路段衔接与起讫仓库", not chain_bad, "; ".join(chain_bad) if chain_bad else "通过")

    # 站点到达时刻与路段一致
    arr_bad = []
    for vid, vstops in sorted(by_veh_stops.items()):
        vlegs = by_veh_legs.get(vid, [])
        visit_stops = [s for s in vstops if s["status"] != "canceled_skip"]
        for k, s in enumerate(visit_stops):
            if k >= len(vlegs) or vlegs[k]["arrive"] != s["arrival"]:
                arr_bad.append(f"{vid}/{s['order_id']}")
    rep.add("行驶·站点到达时刻与路段一致", not arr_bad, "; ".join(arr_bad) if arr_bad else "通过")

    # ---------- 未分配订单均有原因 ----------
    no_reason = [o for o, r in unassigned.items() if not r]
    rep.add("未分配订单均附原因（启发式，不宣称无解）", not no_reason,
            "; ".join(no_reason) if no_reason else f"{len(unassigned)} 单未分配，均有原因")

    conn.close()
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--plan", default="latest", help="计划ID | latest | all")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    if args.plan == "all":
        ids = [r[0] for r in conn.execute("SELECT id FROM plans ORDER BY id")]
    elif args.plan == "latest":
        row = conn.execute("SELECT MAX(id) FROM plans").fetchone()
        ids = [row[0]] if row and row[0] else []
    else:
        ids = [int(args.plan)]
    conn.close()
    if not ids:
        print("数据库中没有计划")
        return 2

    all_ok = True
    for pid in ids:
        rep = validate_plan(args.db, pid)
        all_ok &= rep.ok
        print(f"\n=== 计划 #{pid} 校验 {'通过 ✓' if rep.ok else '失败 ✗'} ===")
        for c in rep.checks:
            mark = "✓" if c["ok"] else "✗"
            if c["ok"] and not args.verbose:
                print(f"  [{mark}] {c['check']}")
            else:
                print(f"  [{mark}] {c['check']} — {c['detail']}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
