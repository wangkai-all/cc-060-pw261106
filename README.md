# 当日订单排车与回放

给当天订单排车，并回放每辆车什么时候到、什么时候回库。行驶时间与里程来自**两份本地矩阵**（去回可不同），全程不调用地图接口。

## 功能

- **排车**：所有车辆从同一仓库出发并返回；初始装载不超容量；每单至多送一次；早到等待，服务开始不晚于承诺时窗止；回库不晚于班次结束。
- **两种方案**：初始方案完全可复现（确定性启发式，无随机数）；改进方案按 **送达订单数 → 车辆数 → 总行驶时间** 的字典序比较。
- **未分配订单**显示原因（超容量 / 无可行插入）；启发式找不到方案时**不宣称无解**。
- **回放**：SVG 路线图上车辆位置随时间移动，时间轴展示行驶/等待/服务/取消；可暂停。
- **暂停改单**：取消尚未开始服务的订单、中途加单。已发生的行驶、已完成/进行中的服务、正在前往的下一站都固定；取消当前下一站仍完成该段行驶但不卸货，取消货物占容量直到回库；**新订单只能给尚未出库的车辆**。
- **费用**：每车启用费 + 每公里费用 × 实际里程。
- **独立校验**：`validate.py` 只读 SQLite，重新核对访问次数、班次、时窗、装载、费用与行驶一致性。

## 目录

```
backend/
  models.py       领域模型（时间统一为午夜起算的秒）
  sample_data.py  五个样例（综合/早到等待/容量不足/非对称/中途加单）
  timeline.py     路线仿真与可行性检查
  solver.py       初始解 + 字典序改进（确定性）
  plan.py         计划展开：站点/路段/事件流
  replan.py       暂停后的取消与加单重规划
  store.py        SQLite 持久化
  validate.py     独立校验程序（仅标准库，可独立运行）
  api.py          FastAPI 接口 + 前端静态托管
  demo.py         端到端命令行演示
  test_vrp.py     15 项测试
frontend/         React + Vite，本地 SVG 路线图与时间轴
data/             SQLite 数据库（运行时生成）
```

## 运行

```bash
# 后端（首次自动加载综合样例并计算初始/改进方案）
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn httpx pytest
cd backend && ../.venv/bin/python -m uvicorn api:app --port 8123

# 前端（开发模式，代理到 8123）
cd frontend && npm install && npm run dev

# 或构建后由后端直接托管（http://127.0.0.1:8123）
cd frontend && npm run build
```

## 验证

```bash
cd backend
../.venv/bin/python -m pytest test_vrp.py -q     # 15 项测试
../.venv/bin/python demo.py                      # 端到端演示（含暂停改单）
../.venv/bin/python demo.py asymmetric           # 指定样例
../.venv/bin/python validate.py --db ../data/app.db --plan all -v   # 独立校验
```

## 回放改单语义

| 操作 | 规则 |
|---|---|
| 取消已完成/进行中服务 | 拒绝，并说明原因 |
| 取消当前下一站（正在前往） | 车辆照常开到，不卸货，随即继续；货物占容量直到回库 |
| 取消更靠后的站 | 不再前往，时刻重算；已出库车辆的货物仍占容量直到回库 |
| 取消未出库车辆的订单 | 不装车，释放容量 |
| 中途加单 | 仅可分配给尚未出库的车辆；全部已出库则显示未分配及原因 |

暂停时刻 T 之前已发生的行驶路段、已完成与进行中的服务逐条保持不变（有测试保证）。

## 主要接口

- `GET /api/state` 当前样例、问题与计划列表
- `POST /api/sample/{name}` 加载样例并重算（main/waiting/capacity/asymmetric/midday）
- `POST /api/plan/rebuild` 基于当前订单重算初始与改进方案
- `GET /api/plan/{id}` 计划详情（站点、路段、事件流、指标）
- `POST /api/plan/{id}/replan` `{time, cancel:[...], add:[...]}` 暂停改单
- `GET /api/plan/{id}/validate` 独立校验报告
