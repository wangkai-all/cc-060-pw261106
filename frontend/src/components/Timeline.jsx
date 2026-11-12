import React, { useMemo, useRef, useState } from 'react'
import { fmt, vehicleColor } from '../api.js'

const LABEL_W = 96, ROW_H = 36, AXIS_H = 22, WIDTH = 980

export default function Timeline({ problem, plan, T, vehicleIds, onSeek }) {
  const [tip, setTip] = useState(null)
  const svgRef = useRef(null)

  const { t0, t1 } = useMemo(() => {
    const starts = problem.vehicles.map(v => v.shift_start)
    const ends = problem.vehicles.map(v => v.shift_end)
    return { t0: Math.min(...starts) - 900, t1: Math.max(...ends) + 900 }
  }, [problem])

  const x = (t) => LABEL_W + ((t - t0) / (t1 - t0)) * (WIDTH - LABEL_W - 8)
  const rows = plan.vehicles.filter(v => v.legs.length)
  const unusedVehicles = problem.vehicles.filter(
    pv => !rows.some(r => r.vehicle_id === pv.id))
  const height = AXIS_H + (rows.length + unusedVehicles.length) * ROW_H + 6

  const hours = []
  for (let h = Math.ceil(t0 / 3600) * 3600; h <= t1; h += 3600) hours.push(h)

  const seek = (e) => {
    const rect = svgRef.current.getBoundingClientRect()
    const px = (e.clientX - rect.left) * (WIDTH / rect.width)
    const t = t0 + ((px - LABEL_W) / (WIDTH - LABEL_W - 8)) * (t1 - t0)
    onSeek(Math.max(t0, Math.min(t1, Math.round(t))))
  }

  const showTip = (e, title, rows) =>
    setTip({ x: e.clientX + 12, y: e.clientY + 12, title, rows })

  const orderById = useMemo(
    () => Object.fromEntries(problem.orders.map(o => [o.id, o])), [problem])

  return (
    <div>
      <div className="legend">
        <span className="item"><span className="swatch box" style={{ background: 'var(--series-1)' }} />服务（车辆色）</span>
        <span className="item"><span className="swatch" style={{ background: 'var(--text-secondary)', height: 3 }} />行驶</span>
        <span className="item"><span className="swatch box" style={{ background: 'var(--wait-fill)' }} />早到等待</span>
        <span className="item"><span className="swatch" style={{ background: 'var(--status-critical)', height: 3 }} />取消货物在车上至回库</span>
      </div>
      <svg ref={svgRef} viewBox={`0 0 ${WIDTH} ${height}`} width="100%"
           style={{ background: 'var(--surface-1)', display: 'block', cursor: 'crosshair' }}
           onClick={seek} role="img" aria-label="车辆时间轴">
        <defs>
          <pattern id="waitHatch" width="6" height="6" patternTransform="rotate(45)"
                   patternUnits="userSpaceOnUse">
            <rect width="6" height="6" fill="var(--wait-fill)" />
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--text-muted)" strokeWidth="1" />
          </pattern>
        </defs>
        {/* 网格与刻度 */}
        {hours.map(h => (
          <g key={h}>
            <line x1={x(h)} y1={AXIS_H - 6} x2={x(h)} y2={height} stroke="var(--gridline)" strokeWidth="1" />
            <text x={x(h)} y={AXIS_H - 9} textAnchor="middle" fontSize="11"
                  fill="var(--text-muted)" style={{ fontVariantNumeric: 'tabular-nums' }}>
              {fmt(h)}
            </text>
          </g>
        ))}
        {rows.map((v, ri) => {
          const y = AXIS_H + ri * ROW_H
          const cy = y + ROW_H / 2
          const color = vehicleColor(vehicleIds, v.vehicle_id)
          const veh = problem.vehicles.find(p => p.id === v.vehicle_id)
          return (
            <g key={v.vehicle_id}>
              <text x={LABEL_W - 8} y={cy + 4} textAnchor="end" fontSize="12"
                    fill="var(--text-primary)">{veh?.name || v.vehicle_id}</text>
              <line x1={LABEL_W} y1={cy} x2={WIDTH - 8} y2={cy}
                    stroke="var(--gridline)" strokeWidth="1" />
              {/* 行驶段 */}
              {v.legs.map((l, i) => (
                <rect key={`l${i}`} x={x(l.depart)} y={cy - 2.5} height="5"
                      width={Math.max(1.5, x(l.arrive) - x(l.depart))} fill={color} opacity="0.55"
                      onMouseMove={e => showTip(e, `行驶 ${fmt(l.depart)}→${fmt(l.arrive)}`,
                        [`${l.km} km｜${Math.round(l.sec / 60)} 分钟`])}
                      onMouseLeave={() => setTip(null)} />
              ))}
              {/* 站点 */}
              {v.stops.map((s, i) => {
                if (s.status === 'canceled_skip') return null
                const o = orderById[s.order_id]
                if (s.status === 'canceled_visit') {
                  return (
                    <g key={`s${i}`}
                       onMouseMove={e => showTip(e, `${o?.name || s.order_id}（已取消）`,
                         [`${fmt(s.arrival)} 到点不卸货`, '货物占容量直到回库'])}
                       onMouseLeave={() => setTip(null)}>
                      <circle cx={x(s.arrival)} cy={cy} r="7" fill="transparent" />
                      <text x={x(s.arrival)} y={cy + 4.5} textAnchor="middle" fontSize="13"
                            fontWeight="700" fill="var(--status-critical)">✕</text>
                    </g>
                  )
                }
                const svcW = x(s.service_end) - x(s.service_start)
                const waitW = s.wait > 0 ? x(s.service_start) - x(s.arrival) : 0
                const label = o?.name || s.order_id
                return (
                  <g key={`s${i}`}>
                    {s.wait > 0 && (
                      <rect x={x(s.arrival)} y={cy - 8} width={Math.max(1, waitW)} height="16"
                            fill="url(#waitHatch)"
                            onMouseMove={e => showTip(e, `早到等待 ${Math.round(s.wait / 60)} 分钟`,
                              [`${fmt(s.arrival)} 到达`, `${fmt(s.service_start)} 时窗开始`])}
                            onMouseLeave={() => setTip(null)} />
                    )}
                    <rect x={x(s.service_start)} y={cy - 10} width={Math.max(2, svcW)} height="20"
                          rx="4" fill={color}
                          onMouseMove={e => showTip(e, label, [
                            `服务 ${fmt(s.service_start)}~${fmt(s.service_end)}`,
                            `时窗 ${fmt(o?.tw_start)}~${fmt(o?.tw_end)}`,
                            `${o?.weight}kg / ${o?.volume}m³`,
                          ])}
                          onMouseLeave={() => setTip(null)} />
                    {svcW > label.length * 13 + 10 && (
                      <text x={x(s.service_start) + 5} y={cy + 4} fontSize="11.5"
                            fill="#fff" pointerEvents="none">{label}</text>
                    )}
                  </g>
                )
              })}
              {/* 取消货物在车上 */}
              {v.stops.some(s => s.cargo_aboard) && plan.pause_time != null && (
                <rect x={x(plan.pause_time)} y={cy + 12} height="3.5"
                      width={Math.max(2, x(v.return_depot) - x(plan.pause_time))}
                      fill="var(--status-critical)" opacity="0.8" />
              )}
              {/* 回库标记 */}
              <text x={x(v.return_depot)} y={cy - 13} fontSize="10.5" textAnchor="middle"
                    fill="var(--text-secondary)">{fmt(v.return_depot)} 回库</text>
            </g>
          )
        })}
        {unusedVehicles.map((pv, i) => {
          const y = AXIS_H + (rows.length + i) * ROW_H
          return (
            <g key={pv.id}>
              <text x={LABEL_W - 8} y={y + ROW_H / 2 + 4} textAnchor="end" fontSize="12"
                    fill="var(--text-muted)">{pv.name}</text>
              <text x={LABEL_W + 8} y={y + ROW_H / 2 + 4} fontSize="11.5"
                    fill="var(--text-muted)">未出车（未收启用费）</text>
            </g>
          )
        })}
        {/* 当前时刻线 */}
        <line x1={x(T)} y1={AXIS_H - 6} x2={x(T)} y2={height}
              stroke="var(--status-critical)" strokeWidth="1.5" />
        <circle cx={x(T)} cy={AXIS_H - 8} r="3.5" fill="var(--status-critical)" />
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
