import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, fmt } from './api.js'
import RouteMap from './components/RouteMap.jsx'
import Timeline from './components/Timeline.jsx'
import OrdersPanel from './components/OrdersPanel.jsx'
import MetricsBar from './components/MetricsBar.jsx'

const KIND_LABEL = { initial: '初始方案', improved: '改进方案', replan: '重规划方案' }

export default function App() {
  const [samples, setSamples] = useState([])
  const [state, setState] = useState(null)       // {sample, problem, plans, latest_plan}
  const [plan, setPlan] = useState(null)         // 当前计划详情
  const [T, setT] = useState(0)                  // 回放时钟（秒）
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(600)        // 每秒推进的仿真秒数
  const [cancelSet, setCancelSet] = useState(new Set())
  const [addList, setAddList] = useState([])
  const [notices, setNotices] = useState([])
  const [checks, setChecks] = useState(null)
  const [error, setError] = useState('')
  const timer = useRef(null)

  const problem = state?.problem
  const vehicleIds = useMemo(
    () => (problem ? problem.vehicles.map(v => v.id).sort() : []), [problem])

  const loadState = useCallback(async () => {
    const s = await api.state()
    setState(s)
    return s
  }, [])

  const openPlan = useCallback(async (id) => {
    const p = await api.plan(id)
    setPlan(p)
    setChecks(null)
    setCancelSet(new Set())
    setAddList([])
    const deps = p.vehicles.flatMap(v => (v.legs.length ? [v.legs[0].depart] : []))
    setT(deps.length ? Math.min(...deps) : 8 * 3600)
    setPlaying(false)
  }, [])

  useEffect(() => {
    (async () => {
      try {
        setSamples(await api.samples())
        const s = await loadState()
        const improved = s.plans.find(p => p.kind === 'improved') || s.plans[s.plans.length - 1]
        if (improved) await openPlan(improved.id)
      } catch (e) { setError(String(e)) }
    })()
  }, [loadState, openPlan])

  // 回放时钟
  useEffect(() => {
    if (!playing || !plan) return
    timer.current = setInterval(() => {
      setT(t => {
        const end = Math.max(...plan.vehicles.map(v => v.return_depot || 0), t)
        const nt = t + speed / 10
        if (nt >= end) { setPlaying(false); return end }
        return nt
      })
    }, 100)
    return () => clearInterval(timer.current)
  }, [playing, speed, plan])

  const switchSample = async (name) => {
    setError(''); setNotices([]); setChecks(null)
    await api.loadSample(name)
    const s = await loadState()
    const improved = s.plans.find(p => p.kind === 'improved')
    if (improved) await openPlan(improved.id)
  }

  const rebuild = async () => {
    setError('')
    const r = await api.rebuild()
    await loadState()
    await openPlan(r.improved)
    setNotices(['已基于当前订单重新计算初始方案与改进方案'])
  }

  const applyReplan = async () => {
    if (!plan) return
    try {
      const body = {
        time: Math.floor(T),
        cancel: [...cancelSet],
        add: addList.map(o => ({ ...o, service: o.service * 60,
                                 tw_start: o.tw_start * 60, tw_end: o.tw_end * 60 })),
      }
      const p = await api.replan(plan.id, body)
      setPlan(p)
      setCancelSet(new Set())
      setAddList([])
      setChecks(null)
      setNotices(p.warnings && p.warnings.length ? p.warnings : ['重规划完成，已从未固定部分继续回放'])
      await loadState()
    } catch (e) { setError(String(e)) }
  }

  const runValidate = async () => {
    if (!plan) return
    const r = await api.validate(plan.id)
    setChecks(r.checks)
  }

  const toggleCancel = (oid) => {
    setCancelSet(s => {
      const n = new Set(s)
      if (n.has(oid)) n.delete(oid); else n.add(oid)
      return n
    })
  }

  if (error) return <div className="app"><div className="notice err">{error}</div></div>
  if (!state || !plan || !problem) return <div className="app"><p className="muted">加载中…</p></div>

  const initialPlanMeta = state.plans.find(p => p.kind === 'initial')
  const editable = !playing
  const pendingEdits = cancelSet.size + addList.length
  const departs = plan.vehicles.map(v => v.depart_depot).filter(x => x != null)
  const returns = plan.vehicles.map(v => v.return_depot).filter(x => x != null)
  const tMin = departs.length ? Math.min(...departs) : 8 * 3600
  const tMax = returns.length ? Math.max(...returns) : 18 * 3600

  return (
    <div className="app">
      <div className="app-header">
        <h1>当日订单排车与回放</h1>
        <span className="sub">本地矩阵 · 不调用地图接口 · 时间均为当日时刻</span>
      </div>

      <div className="toolbar">
        <label>样例
          <select value={state.sample} onChange={e => switchSample(e.target.value)} style={{ marginLeft: 6 }}>
            {samples.map(s => <option key={s.key} value={s.key}>{s.name}</option>)}
          </select>
        </label>
        <label>方案
          <select value={plan.id} onChange={e => openPlan(Number(e.target.value))} style={{ marginLeft: 6 }}>
            {state.plans.map(p => (
              <option key={p.id} value={p.id}>
                #{p.id} {KIND_LABEL[p.kind] || p.kind}
                {p.pause_time != null ? `（暂停于 ${fmt(p.pause_time)}）` : ''}
              </option>
            ))}
          </select>
        </label>
        <button onClick={rebuild} title="用当前全部订单重算初始与改进方案">重新排车</button>
        <button onClick={runValidate}>独立校验</button>
        <span className="hint">
          {KIND_LABEL[plan.kind]} · 目标优先级：送达订单数 → 车辆数 → 总行驶时间
        </span>
      </div>

      <MetricsBar plan={plan} baseline={initialPlanMeta?.metrics} />

      {notices.map((n, i) => <div key={i} className="notice warn">{n}</div>)}
      {plan.note && <div className="notice warn">{plan.note}</div>}

      <div className="card">
        <div className="replay-bar">
          <span className="clock">{fmt(T)}{!playing && <span className="paused">已暂停，可改单</span>}</span>
          <button className="primary" onClick={() => setPlaying(p => !p)}>
            {playing ? '暂停' : '播放'}
          </button>
          <button onClick={() => {
            const deps = plan.vehicles.flatMap(v => (v.legs.length ? [v.legs[0].depart] : []))
            setT(deps.length ? Math.min(...deps) : 0); setPlaying(false)
          }}>回到出库</button>
          <label>速度
            <select value={speed} onChange={e => setSpeed(Number(e.target.value))} style={{ marginLeft: 4 }}>
              <option value={60}>1 分钟/秒</option>
              <option value={300}>5 分钟/秒</option>
              <option value={600}>10 分钟/秒</option>
              <option value={1800}>30 分钟/秒</option>
            </select>
          </label>
          <input type="range" min={tMin} max={tMax}
            value={Math.floor(T)} onChange={e => { setT(Number(e.target.value)); setPlaying(false) }} />
          <button className="primary" disabled={!editable || pendingEdits === 0} onClick={applyReplan}>
            应用改单并重规划{pendingEdits ? `（${pendingEdits} 项）` : ''}
          </button>
        </div>
      </div>

      <div className="layout">
        <div>
          <div className="card">
            <h2>路线图 <span className="hint">车辆位置随回放移动；去回不同路</span></h2>
            <RouteMap problem={problem} plan={plan} T={T} vehicleIds={vehicleIds} />
          </div>
          <div className="card">
            <h2>时间轴 <span className="hint">行驶 / 等待 / 服务 / 取消</span></h2>
            <Timeline problem={problem} plan={plan} T={T} vehicleIds={vehicleIds}
                      onSeek={t => { setT(t); setPlaying(false) }} />
          </div>
        </div>
        <div>
          <OrdersPanel problem={problem} plan={plan} T={T} editable={editable}
                       cancelSet={cancelSet} onToggleCancel={toggleCancel}
                       addList={addList} setAddList={setAddList} />
          {checks && (
            <div className="card">
              <h2>独立校验结果</h2>
              <ul className="checks">
                {checks.map((c, i) => (
                  <li key={i} className={c.ok ? 'ok' : 'fail'}>
                    {c.ok ? '✓' : '✗'} {c.check}{c.ok ? '' : ` — ${c.detail}`}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
