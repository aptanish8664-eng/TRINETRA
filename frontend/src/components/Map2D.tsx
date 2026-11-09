import type { MissionState } from '../types'

export function Map2D({ state, frames, selected, onSelect }: { state: MissionState; frames: MissionState[]; selected: string | null; onSelect: (id: string) => void }) {
  const uavs = Object.values(state.uavs)
  const tasks = Object.values(state.tasks)
  return <div className="map-wrap">
    <div className="map-label top-left"><span className="live-dot" /> ABSTRACT SIMULATION PLANE <span className="map-sep">/</span> 0—10 UNITS</div>
    <svg className="map-svg" viewBox="0 0 1000 1000" role="img" aria-label="Live fleet position and assigned task coordinate map">
      <defs><pattern id="minorGrid" width="50" height="50" patternUnits="userSpaceOnUse"><path d="M 50 0 L 0 0 0 50" fill="none" stroke="#3e6280" strokeOpacity=".21" strokeWidth="1" /></pattern><pattern id="majorGrid" width="250" height="250" patternUnits="userSpaceOnUse"><rect width="250" height="250" fill="url(#minorGrid)"/><path d="M 250 0 L 0 0 0 250" fill="none" stroke="#5b8199" strokeOpacity=".19" strokeWidth="1" /></pattern></defs>
      <rect width="1000" height="1000" fill="url(#majorGrid)" />
      <circle cx="500" cy="500" r="300" fill="none" stroke="#55d9ff" strokeOpacity=".08" strokeDasharray="3 9" />
      <circle cx="500" cy="500" r="440" fill="none" stroke="#55d9ff" strokeOpacity=".06" strokeDasharray="2 10" />
      {tasks.map(task => {
        const owner = task.uav_id ? state.uavs[task.uav_id] : undefined
        return <g key={task.id}>
          {owner && task.status === 'pending' && <line x1={owner.position.x * 100} y1={(10 - owner.position.y) * 100} x2={task.position.x * 100} y2={(10 - task.position.y) * 100} stroke="#55d9ff" strokeOpacity=".22" strokeWidth="1.4" strokeDasharray="6 8" />}
          <circle cx={task.position.x * 100} cy={(10 - task.position.y) * 100} r="7" fill={task.status === 'complete' ? '#56e3b1' : task.uav_id ? '#102c3e' : '#3e2d16'} stroke={task.status === 'complete' ? '#56e3b1' : task.uav_id ? '#55d9ff' : '#f5b94c'} strokeWidth="2" opacity={task.status === 'complete' ? .55 : 1} />
          <text x={task.position.x * 100 + 13} y={(10 - task.position.y) * 100 - 11} className="svg-label">{task.id}</text>
        </g>
      })}
      {uavs.map(uav => {
        const x = uav.position.x * 100, y = (10 - uav.position.y) * 100
        const spoofed = Math.hypot(uav.position.x - uav.gps.x, uav.position.y - uav.gps.y) > .28
        const trail = frames.flatMap(frame => frame.uavs[uav.id] ? [frame.uavs[uav.id]] : [])
        const truthTrack = trail.map(point => `${point.position.x * 100},${(10 - point.position.y) * 100}`).join(' ')
        const gpsTrack = trail.map(point => `${point.gps.x * 100},${(10 - point.gps.y) * 100}`).join(' ')
        const hasDiscrepancy = trail.some(point => Math.hypot(point.position.x - point.gps.x, point.position.y - point.gps.y) > .28)
        return <g key={uav.id} className="uav-map-point" onClick={() => onSelect(uav.id)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(uav.id) } }} tabIndex={0} role="button" aria-pressed={selected === uav.id} aria-label={`Select ${uav.id}`}>
          {trail.length > 1 && <polyline points={truthTrack} fill="none" stroke="#55d9ff" strokeOpacity=".35" strokeWidth="1.3" />}
          {trail.length > 1 && hasDiscrepancy && <polyline points={gpsTrack} fill="none" stroke="#ff4d5a" strokeOpacity=".5" strokeWidth="1.3" strokeDasharray="4 5" />}
          {spoofed && <line x1={x} y1={y} x2={uav.gps.x * 100} y2={(10 - uav.gps.y) * 100} stroke="#ff4d5a" strokeWidth="2.5" strokeDasharray="5 5" />}
          {spoofed && <circle cx={uav.gps.x * 100} cy={(10 - uav.gps.y) * 100} r="7" fill="none" stroke="#ff4d5a" strokeWidth="1.6"><animate attributeName="r" from="7" to="16" dur="1.8s" repeatCount="indefinite"/><animate attributeName="opacity" from=".8" to=".1" dur="1.8s" repeatCount="indefinite"/></circle>}
          <circle cx={x} cy={y} r={selected === uav.id ? 19 : 13} fill="none" stroke={uav.status === 'isolated' ? '#ff4d5a' : selected === uav.id ? '#a7e8ff' : '#55d9ff'} strokeOpacity={selected === uav.id ? '.85' : '.34'} strokeWidth="1" />
          <path d={`M ${x} ${y - 9} l 7 12 -7 -3 -7 3 Z`} fill={uav.status === 'isolated' ? '#ff4d5a' : '#a7e8ff'} transform={`rotate(${uav.heading} ${x} ${y})`} />
          <text x={x + 15} y={y - 15} className="svg-label strong">{uav.id}</text>
        </g>
      })}
    </svg>
    <div className="map-label bottom-left">T+{String(state.time).padStart(4, '0')} SEC <span className="map-sep">/</span> SEED {state.seed}</div>
    <div className="map-legend"><span><i className="legend-plane"/> FLEET</span><span><i className="legend-task"/> TASK</span><span><i className="legend-spoof"/> GPS DISCREPANCY</span></div>
    <div className="map-coordinates"><span>10.0</span><span>05.0</span><span>00.0</span></div>
    <span className="north-mark">N ↑</span>
  </div>
}
