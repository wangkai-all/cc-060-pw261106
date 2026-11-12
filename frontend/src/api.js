const base = ''

async function req(path, opts = {}) {
  const r = await fetch(base + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (!r.ok) {
    const text = await r.text()
    throw new Error(`${r.status}: ${text}`)
  }
  return r.json()
}

export const api = {
  state: () => req('/api/state'),
  samples: () => req('/api/samples'),
  loadSample: (name) => req(`/api/sample/${name}`, { method: 'POST' }),
  plan: (id) => req(`/api/plan/${id}`),
  rebuild: () => req('/api/plan/rebuild', { method: 'POST' }),
  replan: (id, body) => req(`/api/plan/${id}/replan`, { method: 'POST', body: JSON.stringify(body) }),
  validate: (id) => req(`/api/plan/${id}/validate`),
}

export function fmt(sec) {
  if (sec === null || sec === undefined) return '-'
  const s = Math.round(sec)
  const h = String(Math.floor(s / 3600)).padStart(2, '0')
  const m = String(Math.floor((s % 3600) / 60)).padStart(2, '0')
  return `${h}:${m}`
}

export function fmtMin(sec) {
  return `${Math.round(sec / 60)} 分钟`
}

/** 车辆颜色：按车辆 id 排序后的固定槽位，永不循环重用 */
export function vehicleColor(vehicleIds, vid) {
  const i = Math.max(0, vehicleIds.indexOf(vid))
  return `var(--series-${(i % 8) + 1})`
}

/** 车辆虚线样式（次级编码，配合颜色） */
export function vehicleDash(vehicleIds, vid) {
  const pats = ['', '6 3', '2 3', '8 3 2 3', '4 4', '10 4', '3 2', '12 3 3 3']
  const i = Math.max(0, vehicleIds.indexOf(vid))
  return pats[i % pats.length]
}
