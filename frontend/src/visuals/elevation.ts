/** Scenic elevation shown on the Overview. It is decoration that follows the footage (a high snowy peak down to a forest valley);
 * it is NOT telemetry from any aircraft. Change the two constants below to change the top and bottom values. */
export const ELEVATION_TOP_M = 5400
export const ELEVATION_BOTTOM_M = 2300
export const ELEVATION_STEP_M = 10
export const ELEVATION_CAPTION = 'scenic elevation, not live telemetry'

/** Elevation in metres above sea level for a scroll progress of 0 (top of the page) to 1 (the UAV section), rounded to the nearest 10 m.
 * It is clamped between the two constants, so it can never read 0 m. */
export function elevationAt(progress: number): number {
  const p = Number.isFinite(progress) ? Math.max(0, Math.min(1, progress)) : 0
  const raw = ELEVATION_TOP_M + (ELEVATION_BOTTOM_M - ELEVATION_TOP_M) * p
  const rounded = Math.round(raw / ELEVATION_STEP_M) * ELEVATION_STEP_M
  const low = Math.min(ELEVATION_TOP_M, ELEVATION_BOTTOM_M), high = Math.max(ELEVATION_TOP_M, ELEVATION_BOTTOM_M)
  return Math.max(low, Math.min(high, rounded))
}

/** "5,400" (thousands separators, British style). */
export function formatElevation(metres: number): string {
  return metres.toLocaleString('en-GB', { maximumFractionDigits: 0 })
}

export function elevationLabel(progress: number): string {
  return `${formatElevation(elevationAt(progress))} m`
}
