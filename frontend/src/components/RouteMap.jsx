import React, { useMemo, useState } from 'react'
import { fmt, vehicleColor, vehicleDash } from '../api.js'

const W = 100, H = 100, PAD = 10

function pos(n) {
  return { x: n.x, y: H - n.y }   // y 轴翻转，北在上
}

/** 计算 T 时刻车辆位置（在路段上线性插值） */
function vehiclePosAt(vp, nodeById, T) {
  if (!vp.legs.length) return null
  const first = vp.legs[0]
  if (T <= first.depart) return { ...pos(nodeById[first.frm]), state: 'at_depot' }
  for (const leg of vp.legs) {
    if (T >= leg.depart && T < leg.arrive) {
      const a = pos(nodeById[leg.frm]), b = pos(nodeById[leg.to])
      const r = leg.arrive > leg.depart ? (T - leg.depart) / (leg.arrive - leg.depart) : 1
      return { x: a.x + (b.x - a.x) * r, y: a.y + (b.y - a.y) * r, state: 'moving' }
    }
  }
  const last = vp.legs[vp.legs.length - 1]
  return { ...pos(nodeById[last.to]), state: 'returned' }
}

export default function RouteMap({ problem, plan, T, vehicleIds }) {
  const [tip, setTip] = useState(null)
  const nodeById = useMemo(() => Object.fromEntries(problem.nodes.map(n => [n.id, n])), [problem])
  const ordersAt = useMemo(() => {
    const m = {}
    for (const o of problem.orders) (m[o.node] = m[o.node] || []).push(o)
    return m
  }, [problem])

  const showTip = (e, title, rows) =>
    setTip({ x: e.clientX + 12, y: e.clientY + 12, title, rows })
  const hideTip = () => setTip(null)

  const usedVehicles = plan.vehicles.filter(v => v.legs.length)

  return (
    <div>
      <div className="legend">
        {usedVehicles.map(v => {
          const veh = problem.vehicles.find(x => x.id === v.vehicle_id)
          return (
            <span key={v.vehicle_id} className="item">
              <svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3"
                stroke={vehicleColor(vehicleIds, v.vehicle_id)} strokeWidth="2.5"
                strokeDasharray={vehicleDash(vehicleIds, v.vehicle_id)} /></svg>
              {veh?.name || v.vehicle_id}（{v.km} km）
            </span>
          )
        })}
        <span className="item"><svg width="12" height="12"><rect x="2" y="2" width="8" height="8" fill="var(--text-primary)" /></svg>仓库</span>
      </div>
      <svg viewBox={`${-PAD} ${-PAD} ${W + 2 * PAD} ${H + 2 * PAD}`} width="100%"
           style={{ background: 'var(--surface-1)', display: 'block' }}
           role="img" aria-label="车辆路线图">
        {/* 路线 */}
        {usedVehicles.map(v => {
          const color = vehicleColor(vehicleIds, v.vehicle_id)
          const dash = vehicleDash(vehicleIds, v.vehicle_id)
          const pts = v.legs.map(l => {
            const n = pos(nodeById[l.frm])
            return `${n.x},${n.y}`
          })
          const lastLeg = v.legs[v.legs.length - 1]
          const endN = pos(nodeById[lastLeg.to])
          pts.push(`${endN.x},${endN.y}`)
          return (
            <g key={v.vehicle_id}>
              <polyline points={pts.join(' ')} fill="none" stroke={color}
                        strokeWidth="1.2" strokeDasharray={dash} strokeLinejoin="round"
                        strokeLinecap="round" opacity="0.85" />
              {/* 方向箭头：每条路段中点一个（去回方向可见） */}
              {v.legs.map((l, i) => {
                const a = pos(nodeById[l.frm]), b = pos(nodeById[l.to])
                const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2
                const ang = Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI
                return (
                  <path key={i} d="M -1.6 -1.3 L 1.8 0 L -1.6 1.3 Z" fill={color}
                        transform={`translate(${mx} ${my}) rotate(${ang})`} opacity="0.9" />
                )
              })}
            </g>
          )
        })}
        {/* 节点 */}
        {problem.nodes.map(n => {
          const p = pos(n)
          const isDepot = n.id === problem.depot
          const os = ordersAt[n.id] || []
          return (
            <g key={n.id}
               onMouseMove={e => showTip(e, n.name, isDepot ? ['所有车辆由此出发并返回'] :
                 os.map(o => `${o.name}｜${fmt(o.tw_start)}~${fmt(o.tw_end)}｜${o.weight}kg/${o.volume}m³`))}
               onMouseLeave={hideTip}>
              <circle cx={p.x} cy={p.y} r="4.5" fill="transparent" />
              {isDepot
                ? <rect x={p.x - 3.4} y={p.y - 3.4} width="6.8" height="6.8"
                        fill="var(--text-primary)" stroke="var(--surface-1)" strokeWidth="1" />
                : <circle cx={p.x} cy={p.y} r="2.6" fill="var(--surface-1)"
                          stroke="var(--text-secondary)" strokeWidth="1.4" />}
              <text x={p.x} y={p.y + 6.4} textAnchor="middle" fontSize="3.4"
                    fill="var(--text-secondary)">{n.name}</text>
            </g>
          )
        })}
        {/* 站点状态标记（回放推进后出现） */}
        {usedVehicles.flatMap(v => v.stops.map((s, i) => {
          if (s.status === 'canceled_skip') return null
          const p = pos(nodeById[s.node])
          const done = s.service_end != null && T >= s.service_end
          const canceled = s.status === 'canceled_visit' && s.arrival != null && T >= s.arrival
          if (!done && !canceled) return null
          return (
            <text key={`${v.vehicle_id}-${i}`} x={p.x + 3.2} y={p.y - 3.2} fontSize="4.5"
                  fontWeight="700" fill={done ? 'var(--status-good)' : 'var(--status-critical)'}>
              {done ? '✓' : '✕'}
            </text>
          )
        }))}
        {/* 车辆当前位置 */}
        {usedVehicles.map((v, idx) => {
          const p = vehiclePosAt(v, nodeById, T)
          if (!p) return null
          const color = vehicleColor(vehicleIds, v.vehicle_id)
          const veh = problem.vehicles.find(x => x.id === v.vehicle_id)
          const off = p.state !== 'moving' ? idx * 2.2 : 0
          return (
            <g key={v.vehicle_id}
               onMouseMove={e => showTip(e, veh?.name || v.vehicle_id, [
                 `状态：${p.state === 'moving' ? '行驶中' : p.state === 'returned' ? '已回库' : '待出库'}`,
                 `出库 ${fmt(v.depart_depot)}｜回库 ${fmt(v.return_depot)}`,
                 `里程 ${v.km} km｜费用 ¥${v.cost}`,
               ])}
               onMouseLeave={hideTip}>
              <circle cx={p.x + off} cy={p.y} r="4.2" fill="transparent" />
              <circle cx={p.x + off} cy={p.y} r="2.4" fill={color}
                      stroke="var(--surface-1)" strokeWidth="1.2" />
            </g>
          )
        })}
      </svg>
      {tip && (
        <div className="tooltip" style={{ left: tip.x, top: tip.y }}>
          <div className="t-title">{tip.title}</div>
          {tip.rows.map((r, i) => <div key={i} className="t-row">{r}</div>)}
        </div>
      )}
    </div>
  )
}
