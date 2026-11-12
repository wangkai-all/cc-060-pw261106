"""样例数据：早到等待、容量不足、非对称路程、中途加单（综合场景含全部要素）。

矩阵由节点坐标生成，再叠加确定性的方向系数，使去程/回程的时间与里程不同；
不调用任何地图接口。
"""
from __future__ import annotations

import math

from models import Node, Order, Problem, Vehicle, hhmm


def _matrices(nodes: list[Node], asym: bool = True):
    n = len(nodes)
    dist = [[0.0] * n for _ in range(n)]
    tim = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            dx = nodes[i].x - nodes[j].x
            dy = nodes[i].y - nodes[j].y
            base = math.hypot(dx, dy)          # 坐标单位即公里
            if asym:
                # 方向性系数：去回不同（模拟单行道/潮汐车流）
                df = 1.15 if (i - j) % 3 == 0 else (0.90 if (i - j) % 3 == 1 else 1.0)
                tf = 1.30 if (i - j) % 3 == 0 else (0.85 if (i - j) % 3 == 1 else 1.0)
            else:
                df = tf = 1.0
            d = round(base * df, 1)
            dist[i][j] = d
            tim[i][j] = int(round(d * 1.5 * tf * 60))   # 40km/h 基准 -> 秒
    return tim, dist


def sample_main() -> Problem:
    """综合场景：含早到等待、超容量未分配、非对称矩阵；V3 班次晚，适合演示中途加单。"""
    nodes = [
        Node(0, "仓库", 50, 50),
        Node(1, "客户·北苑", 62, 78),
        Node(2, "客户·东湖", 78, 60),
        Node(3, "客户·西桥", 22, 62),
        Node(4, "客户·南港", 40, 22),
        Node(5, "客户·高新", 70, 30),
        Node(6, "客户·老城", 30, 40),
        Node(7, "客户·机场", 88, 82),
        Node(8, "客户·大学城", 12, 84),
        Node(9, "客户·保税区", 60, 8),
        Node(10, "客户·滨江", 82, 44),
    ]
    tim, dist = _matrices(nodes)
    vehicles = [
        Vehicle("V1", "1号小货车", 800, 6.0, hhmm(8), hhmm(16), 100.0, 2.0),
        Vehicle("V2", "2号中货车", 1500, 10.0, hhmm(8), hhmm(16), 150.0, 2.8),
        Vehicle("V3", "3号晚班车", 1000, 8.0, hhmm(10), hhmm(18), 120.0, 2.2),
    ]
    orders = [
        Order("A01", "北苑-晨光订单", 1, 200, 1.5, 600, hhmm(8, 30), hhmm(9, 30)),
        Order("A02", "东湖-生鲜", 2, 150, 1.0, 600, hhmm(9), hhmm(10)),
        Order("A03", "西桥-午前件", 3, 300, 2.0, 900, hhmm(11), hhmm(12, 30)),   # 早到等待
        Order("A04", "南港-文件", 4, 100, 0.8, 600, hhmm(9, 30), hhmm(11)),
        Order("A05", "高新-设备", 5, 250, 1.2, 600, hhmm(10), hhmm(12)),
        Order("A06", "老城-午后件", 6, 400, 2.5, 900, hhmm(13), hhmm(15)),
        Order("A07", "机场-急件", 7, 180, 1.0, 480, hhmm(8, 30), hhmm(9, 30)),
        Order("A08", "大学城-批量", 8, 900, 5.0, 1200, hhmm(9), hhmm(12)),
        Order("A09", "保税区-超重件", 9, 2000, 12.0, 600, hhmm(10), hhmm(14)),   # 超所有车辆容量
        Order("A10", "滨江-傍晚件", 10, 120, 0.6, 600, hhmm(14), hhmm(16)),
    ]
    return Problem(nodes, tim, dist, vehicles, orders)


def sample_waiting() -> Problem:
    """早到等待：车辆早到近 2 小时，必须等到时窗开始才能服务。"""
    nodes = [Node(0, "仓库", 50, 50), Node(1, "客户·远郊", 70, 55),
             Node(2, "客户·近郊", 58, 52)]
    tim, dist = _matrices(nodes, asym=False)
    vehicles = [Vehicle("V1", "1号车", 1000, 8.0, hhmm(8), hhmm(14), 80.0, 1.8)]
    orders = [
        Order("W01", "远郊-预约件", 1, 200, 1.0, 900, hhmm(10, 30), hhmm(12)),  # 早到必等
        Order("W02", "近郊-回程件", 2, 100, 0.5, 600, hhmm(12), hhmm(13, 30)),
    ]
    return Problem(nodes, tim, dist, vehicles, orders)


def sample_capacity() -> Problem:
    """容量不足：一单超所有车辆载重，一单超容积，均显示未分配及原因。"""
    nodes = [Node(0, "仓库", 50, 50), Node(1, "客户·甲", 60, 60),
             Node(2, "客户·乙", 40, 62), Node(3, "客户·丙", 55, 40)]
    tim, dist = _matrices(nodes, asym=False)
    vehicles = [Vehicle("V1", "1号车", 500, 4.0, hhmm(8), hhmm(16), 80.0, 1.8),
                Vehicle("V2", "2号车", 800, 6.0, hhmm(8), hhmm(16), 120.0, 2.4)]
    orders = [
        Order("C01", "甲-正常件", 1, 200, 1.0, 600, hhmm(9), hhmm(11)),
        Order("C02", "乙-超重件", 2, 1200, 2.0, 600, hhmm(9), hhmm(12)),   # 超重
        Order("C03", "丙-超大件", 3, 100, 9.0, 600, hhmm(9), hhmm(12)),    # 超容积
    ]
    return Problem(nodes, tim, dist, vehicles, orders)


def sample_asymmetric() -> Problem:
    """非对称路程：去程 10 分钟、回程 50 分钟；排程与费用都必须按方向取矩阵值。"""
    nodes = [Node(0, "仓库", 20, 50), Node(1, "客户·谷东", 80, 50),
             Node(2, "客户·谷西", 45, 80)]
    tim, dist = _matrices(nodes, asym=False)
    # 手工覆盖：0->1 快速路 10 分钟/12km，1->0 绕行 50 分钟/30km
    tim[0][1] = 600
    dist[0][1] = 12.0
    tim[1][0] = 3000
    dist[1][0] = 30.0
    # 2->1 方向远快于 1->2
    tim[2][1] = 480
    dist[2][1] = 9.0
    tim[1][2] = 2400
    dist[1][2] = 26.0
    vehicles = [Vehicle("V1", "1号车", 1000, 8.0, hhmm(8), hhmm(12), 60.0, 1.5)]
    orders = [
        Order("S01", "谷东-去程件", 1, 200, 1.0, 600, hhmm(8, 30), hhmm(9, 30)),
        Order("S02", "谷西-顺路件", 2, 150, 0.8, 600, hhmm(9), hhmm(10, 30)),
    ]
    return Problem(nodes, tim, dist, vehicles, orders)


def sample_midday() -> Problem:
    """中途加单：V2 班次 11 点才出库，回放暂停在 09:30 时可给它加单。"""
    nodes = [
        Node(0, "仓库", 50, 50),
        Node(1, "客户·早市", 64, 66),
        Node(2, "客户·车站", 36, 60),
        Node(3, "客户·园区", 66, 36),
        Node(4, "客户·码头", 30, 30),
    ]
    tim, dist = _matrices(nodes)
    vehicles = [
        Vehicle("V1", "早班车", 600, 5.0, hhmm(8), hhmm(14), 90.0, 1.9),
        Vehicle("V2", "午班车", 900, 7.0, hhmm(11), hhmm(18), 110.0, 2.1),
    ]
    orders = [
        Order("M01", "早市-第一批", 1, 200, 1.5, 600, hhmm(8, 30), hhmm(9, 30)),
        Order("M02", "车站-上午件", 2, 150, 1.0, 600, hhmm(9), hhmm(10, 30)),
        Order("M03", "园区-午后件", 3, 300, 2.0, 900, hhmm(13), hhmm(15)),
        Order("M04", "码头-傍晚件", 4, 250, 1.5, 600, hhmm(15), hhmm(17)),
    ]
    return Problem(nodes, tim, dist, vehicles, orders)


SAMPLES = {
    "main": ("综合演示（等待/超容量/非对称/可加单）", sample_main),
    "waiting": ("早到等待", sample_waiting),
    "capacity": ("容量不足", sample_capacity),
    "asymmetric": ("非对称路程", sample_asymmetric),
    "midday": ("中途加单", sample_midday),
}


def load_sample(name: str) -> Problem:
    return SAMPLES[name][1]()
