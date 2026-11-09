export function BrandMark({ compact = false }: { compact?: boolean }) {
  return <div className={`brand-lockup ${compact ? 'brand-compact' : ''}`} aria-label="TRINETRA">
    <svg className="brand-eye" viewBox="0 0 54 40" role="img" aria-hidden="true">
      <path d="M3 20 12 9h30l9 11-9 11H12L3 20Z" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M14 20h26M20 15l7 5 7-5M20 25l7-5 7 5" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="27" cy="20" r="5.2" fill="#55d9ff" fillOpacity=".16" stroke="#55d9ff" strokeWidth="1.2" />
      <circle cx="27" cy="20" r="1.7" fill="#a7e8ff" />
    </svg>
    {!compact && <span><b>TRINETRA</b><small>AUTONOMOUS AERIAL INTELLIGENCE</small></span>}
  </div>
}
