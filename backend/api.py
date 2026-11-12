"""FastAPI 接口：样例加载、初始/改进计划、回放暂停后的取消与加单、校验报告。"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import store
import validate
from models import (ASSIGNED, CANCELED, DONE, PENDING, UNASSIGNED, Order,
                    Problem, fmt_time)
from plan import (ST_CANCEL_SKIP, ST_CANCEL_VISIT, ST_DONE, ST_PLANNED,
                  ST_SERVING, Plan, build_plan, vehicle_events)
from replan import replan
from sample_data import SAMPLES, load_sample
from solver import Solution, improve, initial_solution

BASE = Path(__file__).resolve().parent
DB_PATH = os.environ.get("VRP_DB", str(BASE.parent / "data" / "app.db"))
FRONTEND_DIST = BASE.parent / "frontend" / "dist"

app = FastAPI(title="当日订单排车与回放")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


def db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    return store.connect(DB_PATH)


# ---------------------------------------------------------------- 序列化

def problem_json(prob: Problem) -> dict:
    return {
        "depot": prob.depot,
        "nodes": [dict(id=n.id, name=n.name, x=n.x, y=n.y) for n in prob.nodes],
        "vehicles": [dict(id=v.id, name=v.name, cap_weight=v.cap_weight,
                          cap_volume=v.cap_volume, shift_start=v.shift_start,
                          shift_end=v.shift_end, activation_fee=v.activation_fee,
                          cost_per_km=v.cost_per_km) for v in prob.vehicles],
        "orders": [dict(id=o.id, name=o.name, node=o.node, weight=o.weight,
                        volume=o.volume, service=o.service, tw_start=o.tw_start,
                        tw_end=o.tw_end, status=o.status, reason=o.reason,
                        cancel_time=o.cancel_time) for o in prob.orders],
    }


def plan_json(conn, plan: Plan, plan_id: int, prob: Problem) -> dict:
    vehicles = []
    for vid, vp in sorted(plan.vehicles.items()):
        vehicles.append({
            "vehicle_id": vid,
            "depart_depot": vp.depart_depot,
            "return_depot": vp.return_depot,
            "km": vp.km, "sec": vp.sec, "cost": vp.cost,
            "stops": [dict(order_id=s.order_id, node=s.node, status=s.status,
                           arrival=s.arrival, service_start=s.service_start,
                           service_end=s.service_end, depart=s.depart, wait=s.wait,
                           cargo_aboard=s.cargo_aboard) for s in vp.stops],
            "legs": [dict(frm=l.frm, to=l.to, depart=l.depart, arrive=l.arrive,
                          km=l.km, sec=l.sec) for l in vp.legs],
            "events": vehicle_events(prob, vp),
        })
    return {"id": plan_id, "kind": plan.kind, "pause_time": plan.pause_time,
            "note": plan.note, "metrics": plan.metrics(prob),
            "vehicles": vehicles, "unassigned": plan.unassigned,
            "canceled": plan.canceled}


def sync_order_statuses(prob: Problem, plan: Plan) -> None:
    omap = prob.order_map()
    for o in prob.orders:
        if o.status != CANCELED:
            o.status = PENDING
    for vp in plan.vehicles.values():
        for s in vp.stops:
            o = omap[s.order_id]
            if s.status == ST_DONE:
                o.status = DONE
            elif s.status in (ST_CANCEL_VISIT, ST_CANCEL_SKIP):
                o.status = CANCELED
            else:
                o.status = ASSIGNED
    for oid, reason in plan.unassigned.items():
        omap[oid].status = UNASSIGNED
        omap[oid].reason = reason
    for oid in plan.canceled:
        omap[oid].status = CANCELED


def solve_and_store(conn, prob: Problem) -> tuple[int, int]:
    """重算初始解与改进解并入库，返回两个计划ID。"""
    for o in prob.orders:
        o.status = PENDING
        o.reason = ""
        o.cancel_time = None
    sol0 = initial_solution(prob)
    plan0 = build_plan(prob, sol0, "initial")
    sync_order_statuses(prob, plan0)
    pid0 = store.save_plan(conn, plan0, prob)
    sol1 = improve(prob, sol0)
    plan1 = build_plan(prob, sol1, "improved")
    sync_order_statuses(prob, plan1)
    pid1 = store.save_plan(conn, plan1, prob)
    store.save_orders(conn, prob)
    return pid0, pid1


# ---------------------------------------------------------------- 接口

@app.on_event("startup")
def startup():
    conn = db()
    if conn.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"] == 0:
        prob = load_sample("main")
        store.save_problem(conn, prob, "main")
        solve_and_store(conn, prob)
    conn.close()


@app.get("/api/samples")
def samples():
    return [{"key": k, "name": v[0]} for k, v in SAMPLES.items()]


@app.post("/api/sample/{name}")
def load(name: str):
    if name not in SAMPLES:
        raise HTTPException(404, f"未知样例 {name}")
    conn = db()
    store.reset(conn)
    prob = load_sample(name)
    store.save_problem(conn, prob, name)
    pid0, pid1 = solve_and_store(conn, prob)
    plans = store.list_plans(conn)
    conn.close()
    return {"sample": name, "plans": plans, "initial": pid0, "improved": pid1}


@app.get("/api/state")
def state():
    conn = db()
    prob = store.load_problem(conn)
    sample = conn.execute("SELECT value FROM meta WHERE key='sample'").fetchone()
    out = {"sample": sample["value"] if sample else "",
           "problem": problem_json(prob),
           "plans": store.list_plans(conn),
           "latest_plan": store.latest_plan_id(conn)}
    conn.close()
    return out


@app.post("/api/plan/rebuild")
def rebuild():
    """基于当前订单（含中途加入的）重新排初始解与改进解。"""
    conn = db()
    prob = store.load_problem(conn)
    pid0, pid1 = solve_and_store(conn, prob)
    plans = store.list_plans(conn)
    conn.close()
    return {"initial": pid0, "improved": pid1, "plans": plans}


@app.get("/api/plan/{plan_id}")
def get_plan(plan_id: int):
    conn = db()
    prob = store.load_problem(conn)
    plan = store.load_plan(conn, plan_id)
    if plan is None:
        conn.close()
        raise HTTPException(404, "计划不存在")
    out = plan_json(conn, plan, plan_id, prob)
    conn.close()
    return out


class NewOrder(BaseModel):
    id: str | None = None
    name: str
    node: int
    weight: float
    volume: float
    service: int          # 秒
    tw_start: int
    tw_end: int


class ReplanReq(BaseModel):
    time: int                       # 回放暂停时刻（秒）
    cancel: list[str] = []          # 取消的订单
    add: list[NewOrder] = []        # 新增订单


@app.post("/api/plan/{plan_id}/replan")
def do_replan(plan_id: int, req: ReplanReq):
    conn = db()
    prob = store.load_problem(conn)
    plan = store.load_plan(conn, plan_id)
    if plan is None:
        conn.close()
        raise HTTPException(404, "计划不存在")
    node_ids = {n.id for n in prob.nodes}
    existing = {o.id for o in prob.orders}
    new_orders: list[Order] = []
    for i, no in enumerate(req.add):
        if no.node not in node_ids:
            conn.close()
            raise HTTPException(400, f"订单 {no.name} 的节点 {no.node} 不存在")
        oid = no.id or f"N{len(existing) + i + 1:03d}"
        if oid in existing:
            conn.close()
            raise HTTPException(400, f"订单ID {oid} 已存在")
        if no.weight < 0 or no.volume < 0 or no.service < 0 or no.tw_end < no.tw_start:
            conn.close()
            raise HTTPException(400, f"订单 {no.name} 参数非法")
        new_orders.append(Order(oid, no.name, no.node, no.weight, no.volume,
                                no.service, no.tw_start, no.tw_end))
    result = replan(prob, plan, req.time, req.cancel, new_orders)
    sync_order_statuses(prob, result.plan)
    store.save_orders(conn, prob)
    pid = store.save_plan(conn, result.plan, prob)
    out = plan_json(conn, result.plan, pid, prob)
    out["warnings"] = result.warnings
    out["rejected_cancels"] = result.rejected_cancels
    conn.close()
    return out


@app.get("/api/plan/{plan_id}/validate")
def validate_plan(plan_id: int):
    rep = validate.validate_plan(DB_PATH, plan_id)
    return {"ok": rep.ok, "checks": rep.checks}


# ---------------------------------------------------------------- 前端静态资源

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        f = FRONTEND_DIST / full_path
        if full_path and f.exists() and f.is_file():
            return FileResponse(f)
        return FileResponse(FRONTEND_DIST / "index.html")
