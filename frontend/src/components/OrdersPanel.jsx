import React, { useMemo, useState } from 'react'
import { fmt } from '../api.js'

const STATUS_TAG = {
  assigned: ['assigned', '已分配'],
  unassigned: ['unassigned', '未分配'],
  canceled: ['canceled', '已取消'],
  done: ['done', '已完成'],
  pending: ['pending', '待排'],
}

function toMin(hhmmStr) {
  const [h, m] = hhmmStr.split(':').map(Number)
  return h * 60 + m
}
function toHHMM(minOfDay) {
  return `${String(Math.floor(minOfDay / 60)).padStart(2, '0')}:${String(minOfDay % 60).padStart(2, '0')}`
}

export default function OrdersPanel({ problem, plan, T, editable,
                                      cancelSet, onToggleCancel, addList, setAddList }) {
  const firstNode = problem.nodes.find(n => n.id !== problem.depot)?.id ?? 1
  const [form, setForm] = useState({
    name: '', node: firstNode, weight: 100, volume: 1,
    service: 10, tw_start: '13:00', tw_end: '16:00',
  })

  // 订单 -> 计划中的站点与车辆
  const stopOf = useMemo(() => {
    const m = {}
    for (const v of plan.vehicles)
      for (const s of v.stops)
        m[s.order_id] = { vehicle_id: v.vehicle_id, ...s }
    return m
  }, [plan])

  const vehicleName = (vid) =>
    problem.vehicles.find(v => v.id === vid)?.name || vid

  // 可取消：已分配且服务尚未开始；或未分配（视为放弃）
  const cancellable = (o) => {
    if (plan.canceled.includes(o.id) || cancelSet.has(o.id)) return false
    const s = stopOf[o.id]
    if (s) {
      if (s.status === 'done' || s.status === 'serving') return false
      if (s.service_start != null && s.service_start <= T) return false
      return s.status === 'planned'
    }
    return o.id in plan.unassigned
  }

  const addOrder = () => {
    if (!form.name.trim()) return
    setAddList(list => [...list, {
      name: form.name.trim(), node: Number(form.node),
      weight: Number(form.weight), volume: Number(form.volume),
      service: Number(form.service),
      tw_start: toMin(form.tw_start), tw_end: toMin(form.tw_end),
    }])
    setForm(f => ({ ...f, name: '' }))
  }

  return (
    <>
      <div className="card">
        <h2>订单 <span className="hint">暂停后可取消未开始服务的订单</span></h2>
        <table>
          <thead>
            <tr><th>订单</th><th>时窗</th><th>重量/体积</th><th>状态</th><th>车辆</th><th></th></tr>
          </thead>
          <tbody>
            {problem.orders.map(o => {
              const s = stopOf[o.id]
              const tag = STATUS_TAG[o.status] || ['pending', o.status]
              const inCancel = cancelSet.has(o.id)
              return (
                <tr key={o.id} style={inCancel ? { opacity: 0.55 } : undefined}>
                  <td>{o.name}</td>
                  <td>{fmt(o.tw_start)}~{fmt(o.tw_end)}</td>
                  <td>{o.weight}kg / {o.volume}m³</td>
                  <td>
                    <span className={`tag ${inCancel ? 'canceled' : tag[0]}`}>
                      {inCancel ? '待取消' : tag[1]}
                    </span>
                    {o.status === 'unassigned' && o.reason && (
                      <div className="reason">{o.reason}</div>
                    )}
                  </td>
                  <td>{s ? vehicleName(s.vehicle_id) : '—'}</td>
                  <td>
                    {editable && (cancellable(o) || inCancel) && (
                      <button className={inCancel ? '' : 'danger'}
                              onClick={() => onToggleCancel(o.id)}>
                        {inCancel ? '撤销' : '取消'}
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
            {addList.map((o, i) => (
              <tr key={`new-${i}`}>
                <td>{o.name} <span className="tag pending">新单</span></td>
                <td>{toHHMM(o.tw_start)}~{toHHMM(o.tw_end)}</td>
                <td>{o.weight}kg / {o.volume}m³</td>
                <td><span className="tag pending">待分配</span></td>
                <td>仅未出库车辆</td>
                <td><button onClick={() => setAddList(l => l.filter((_, j) => j !== i))}>移除</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h2>中途加单 <span className="hint">仅可分配给尚未出库的车辆；暂停时可用</span></h2>
        <div className="add-form">
          <div>
            <label>名称</label>
            <input value={form.name} placeholder="如：临时加单"
                   onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
          </div>
          <div>
            <label>送达点</label>
            <select value={form.node} onChange={e => setForm(f => ({ ...f, node: e.target.value }))}>
              {problem.nodes.filter(n => n.id !== problem.depot).map(n => (
                <option key={n.id} value={n.id}>{n.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label>重量 kg</label>
            <input type="number" min="0" value={form.weight}
                   onChange={e => setForm(f => ({ ...f, weight: e.target.value }))} />
          </div>
          <div>
            <label>体积 m³</label>
            <input type="number" min="0" step="0.1" value={form.volume}
                   onChange={e => setForm(f => ({ ...f, volume: e.target.value }))} />
          </div>
          <div>
            <label>服务时长（分钟）</label>
            <input type="number" min="0" value={form.service}
                   onChange={e => setForm(f => ({ ...f, service: e.target.value }))} />
          </div>
          <div>
            <label>时窗起</label>
            <input type="time" value={form.tw_start}
                   onChange={e => setForm(f => ({ ...f, tw_start: e.target.value }))} />
          </div>
          <div>
            <label>时窗止</label>
            <input type="time" value={form.tw_end}
                   onChange={e => setForm(f => ({ ...f, tw_end: e.target.value }))} />
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end' }}>
            <button className="primary" disabled={!editable || !form.name.trim()}
                    onClick={addOrder}>加入待分配</button>
          </div>
        </div>
        {!editable && <div className="hint" style={{ marginTop: 6 }}>回放进行中，暂停后才能加单。</div>}
      </div>
    </>
  )
}
