import React from 'react'

function Delta({ cur, base, invert = false, unit = '' }) {
  if (base == null || cur == null || base === cur) return null
  const d = cur - base
  // invert=true 表示数值小为好（车辆数、行驶时间、费用）
  const good = invert ? d < 0 : d > 0
  return (
    <div className={`delta ${good ? 'good' : 'bad'}`}>
      {d > 0 ? '+' : ''}{d}{unit}（对比初始方案）
    </div>
  )
}

export default function MetricsBar({ plan, baseline }) {
  const m = plan.metrics
  const b = plan.kind === 'initial' ? null : baseline
  const travelMin = Math.round(m.travel_sec / 60)
  const baseTravelMin = b ? Math.round(b.travel_sec / 60) : null
  return (
    <div className="stat-row">
      <div className="stat">
        <div className="label">送达订单</div>
        <div className="value">{m.delivered}</div>
        <Delta cur={m.delivered} base={b?.delivered} unit=" 单" />
      </div>
      <div className="stat">
        <div className="label">出车数量</div>
        <div className="value">{m.vehicles}</div>
        <Delta cur={m.vehicles} base={b?.vehicles} invert unit=" 辆" />
      </div>
      <div className="stat">
        <div className="label">总行驶时间</div>
        <div className="value">{travelMin}<span style={{ fontSize: 14 }}> 分钟</span></div>
        <Delta cur={travelMin} base={baseTravelMin} invert unit=" 分钟" />
      </div>
      <div className="stat">
        <div className="label">总里程</div>
        <div className="value">{m.distance_km}<span style={{ fontSize: 14 }}> km</span></div>
      </div>
      <div className="stat">
        <div className="label">总费用（启用费+里程费）</div>
        <div className="value">¥{m.cost}</div>
        <Delta cur={m.cost} base={b?.cost} invert unit=" 元" />
      </div>
      {(m.unassigned > 0 || m.canceled > 0) && (
        <div className="stat">
          <div className="label">未分配 / 已取消</div>
          <div className="value">{m.unassigned} / {m.canceled}</div>
        </div>
      )}
    </div>
  )
}
