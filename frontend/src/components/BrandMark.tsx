/** The final TRINETRA logo (files in public/brand, supplied as-is: never redrawn or recoloured here).
 * `compact` uses the mark alone where space is tight; otherwise the full lockup is used. "dark" artwork is for dark surfaces, "light" for light ones. */
export const BRAND = {
  lockup: { dark: '/brand/trinetra-logo-dark.svg', light: '/brand/trinetra-logo-light.svg' },
  mark: { dark: '/brand/trinetra-mark-dark.svg', light: '/brand/trinetra-mark-light.svg' },
} as const

export function BrandMark({ compact = false, surface = 'dark', height }: { compact?: boolean; surface?: 'dark' | 'light'; height?: number }) {
  const src = (compact ? BRAND.mark : BRAND.lockup)[surface]
  const size = height ?? (compact ? 28 : 46)
  return <span className={`brand-lockup ${compact ? 'brand-compact' : ''}`}>
    <img className="brand-img" src={src} alt="TRINETRA" height={size} style={{ height: size, width: 'auto' }} decoding="async" draggable={false} />
  </span>
}
