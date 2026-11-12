"""SQLite 持久化：问题实例、矩阵、计划（站点/路段/未分配）。校验程序只读本库。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from models import (ASSIGNED, CANCELED, DONE, PENDING, UNASSIGNED,
                    Node, Order, Problem, Vehicle)
from plan import (Leg, Plan, ST_CANCEL_SKIP, ST_CANCEL_VISIT, ST_DONE,
                  ST_PLANNED, ST_SERVING, StopPlan, VehiclePlan)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS nodes(id INTEGER PRIMARY KEY, name TEXT, x REAL, y REAL, is_depot INT);
CREATE TABLE IF NOT EXISTS vehicles(id TEXT PRIMARY KEY, name TEXT, cap_w REAL, cap_v REAL,
    shift_start INT, shift_end INT, fee REAL, per_km REAL);
CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, name TEXT, node INT, weight REAL,
    volume REAL, service INT, tw_start INT, tw_end INT, status TEXT, reason TEXT, cancel_time INT);
CREATE TABLE IF NOT EXISTS matrix_time(src INT, dst INT, sec INT, PRIMARY KEY(src, dst));
CREATE TABLE IF NOT EXISTS matrix_dist(src INT, dst INT, km REAL, PRIMARY KEY(src, dst));
CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT,
    pause_time INT, note TEXT, metrics TEXT, created TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS stops(plan_id INT, vehicle_id TEXT, seq INT, order_id TEXT, node INT,
    status TEXT, arrival INT, svc_start INT, svc_end INT, depart INT, wait INT, cargo_aboard INT);
CREATE TABLE IF NOT EXISTS legs(plan_id INT, vehicle_id TEXT, seq INT, src INT, dst INT,
    depart INT, arrive INT, km REAL, sec INT);
CREATE TABLE IF NOT EXISTS unassigned(plan_id INT, order_id TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS canceled(plan_id INT, order_id TEXT);
"""

CLEAR_TABLES = ["nodes", "vehicles", "orders", "matrix_time", "matrix_dist",
                "plans", "stops", "legs", "unassigned", "canceled"]


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def reset(conn: sqlite3.Connection) -> None:
    for t in CLEAR_TABLES:
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM meta")
    conn.commit()


def save_problem(conn: sqlite3.Connection, prob: Problem, sample: str = "") -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('sample', ?)", (sample,))
    for n in prob.nodes:
        conn.execute("INSERT INTO nodes VALUES(?,?,?,?,?)",
                     (n.id, n.name, n.x, n.y, 1 if n.id == prob.depot else 0))
    for v in prob.vehicles:
        conn.execute("INSERT INTO vehicles VALUES(?,?,?,?,?,?,?,?)",
                     (v.id, v.name, v.cap_weight, v.cap_volume,
                      v.shift_start, v.shift_end, v.activation_fee, v.cost_per_km))
    save_orders(conn, prob)
    for i in range(len(prob.nodes)):
        for j in range(len(prob.nodes)):
            conn.execute("INSERT INTO matrix_time VALUES(?,?,?)", (i, j, prob.time_matrix[i][j]))
            conn.execute("INSERT INTO matrix_dist VALUES(?,?,?)", (i, j, prob.dist_matrix[i][j]))
    conn.commit()


def save_orders(conn: sqlite3.Connection, prob: Problem) -> None:
    for o in prob.orders:
        conn.execute("""INSERT OR REPLACE INTO orders
            (id,name,node,weight,volume,service,tw_start,tw_end,status,reason,cancel_time)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                     (o.id, o.name, o.node, o.weight, o.volume, o.service,
                      o.tw_start, o.tw_end, o.status, o.reason, o.cancel_time))
    conn.commit()


def load_problem(conn: sqlite3.Connection) -> Problem:
    nodes = [Node(r["id"], r["name"], r["x"], r["y"])
             for r in conn.execute("SELECT * FROM nodes ORDER BY id")]
    depot_row = conn.execute("SELECT id FROM nodes WHERE is_depot=1").fetchone()
    depot = depot_row["id"] if depot_row else 0
    n = len(nodes)
    tim = [[0] * n for _ in range(n)]
    dist = [[0.0] * n for _ in range(n)]
    for r in conn.execute("SELECT * FROM matrix_time"):
        tim[r["src"]][r["dst"]] = r["sec"]
    for r in conn.execute("SELECT * FROM matrix_dist"):
        dist[r["src"]][r["dst"]] = r["km"]
    vehicles = [Vehicle(r["id"], r["name"], r["cap_w"], r["cap_v"], r["shift_start"],
                        r["shift_end"], r["fee"], r["per_km"])
                for r in conn.execute("SELECT * FROM vehicles ORDER BY id")]
    orders = [Order(r["id"], r["name"], r["node"], r["weight"], r["volume"], r["service"],
                    r["tw_start"], r["tw_end"], r["status"], r["reason"], r["cancel_time"])
              for r in conn.execute("SELECT * FROM orders ORDER BY id")]
    return Problem(nodes, tim, dist, vehicles, orders, depot)


def save_plan(conn: sqlite3.Connection, plan: Plan, prob: Problem) -> int:
    cur = conn.execute("INSERT INTO plans(kind, pause_time, note, metrics) VALUES(?,?,?,?)",
                       (plan.kind, plan.pause_time, plan.note,
                        json.dumps(plan.metrics(prob), ensure_ascii=False)))
    pid = cur.lastrowid
    for vid, vp in plan.vehicles.items():
        for i, s in enumerate(vp.stops):
            conn.execute("INSERT INTO stops VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (pid, vid, i, s.order_id, s.node, s.status, s.arrival,
                          s.service_start, s.service_end, s.depart, s.wait,
                          1 if s.cargo_aboard else 0))
        for i, l in enumerate(vp.legs):
            conn.execute("INSERT INTO legs VALUES(?,?,?,?,?,?,?,?,?)",
                         (pid, vid, i, l.frm, l.to, l.depart, l.arrive, l.km, l.sec))
    for oid, reason in plan.unassigned.items():
        conn.execute("INSERT INTO unassigned VALUES(?,?,?)", (pid, oid, reason))
    for oid in plan.canceled:
        conn.execute("INSERT INTO canceled VALUES(?,?)", (pid, oid))
    conn.commit()
    return pid


def load_plan(conn: sqlite3.Connection, plan_id: int) -> Plan | None:
    row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        return None
    plan = Plan(kind=row["kind"], pause_time=row["pause_time"], note=row["note"] or "")
    for r in conn.execute("SELECT * FROM stops WHERE plan_id=? ORDER BY vehicle_id, seq",
                          (plan_id,)):
        vp = plan.vehicles.setdefault(r["vehicle_id"], VehiclePlan(vehicle_id=r["vehicle_id"]))
        vp.stops.append(StopPlan(r["order_id"], r["node"], r["status"], r["arrival"],
                                 r["svc_start"], r["svc_end"], r["depart"], r["wait"],
                                 bool(r["cargo_aboard"])))
    for r in conn.execute("SELECT * FROM legs WHERE plan_id=? ORDER BY vehicle_id, seq",
                          (plan_id,)):
        vp = plan.vehicles.setdefault(r["vehicle_id"], VehiclePlan(vehicle_id=r["vehicle_id"]))
        vp.legs.append(Leg(r["src"], r["dst"], r["depart"], r["arrive"], r["km"], r["sec"]))
    for vid, vp in plan.vehicles.items():
        if vp.legs:
            vp.depart_depot = vp.legs[0].depart
            vp.return_depot = vp.legs[-1].arrive
            vp.km = round(sum(l.km for l in vp.legs), 3)
            vp.sec = sum(l.sec for l in vp.legs)
    vmap = {r["id"]: r for r in conn.execute("SELECT * FROM vehicles")}
    for vid, vp in plan.vehicles.items():
        if vp.legs and vid in vmap:
            vp.cost = round(vmap[vid]["fee"] + vmap[vid]["per_km"] * vp.km, 2)
    plan.unassigned = {r["order_id"]: r["reason"]
                       for r in conn.execute("SELECT * FROM unassigned WHERE plan_id=?", (plan_id,))}
    plan.canceled = [r["order_id"]
                     for r in conn.execute("SELECT * FROM canceled WHERE plan_id=?", (plan_id,))]
    return plan


def list_plans(conn: sqlite3.Connection) -> list[dict]:
    return [dict(id=r["id"], kind=r["kind"], pause_time=r["pause_time"],
                 note=r["note"], metrics=json.loads(r["metrics"]), created=r["created"])
            for r in conn.execute("SELECT * FROM plans ORDER BY id")]


def latest_plan_id(conn: sqlite3.Connection) -> int | None:
    r = conn.execute("SELECT MAX(id) AS m FROM plans").fetchone()
    return r["m"]
