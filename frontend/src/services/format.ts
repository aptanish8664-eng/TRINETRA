/** British-style date and time text for the interface: "18 NOV 2026 14:05" (24-hour clock, day first, three-letter month). Shown in the browser's time zone. */
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
const two = (value: number) => String(value).padStart(2, '0')

export function formatDate(value: string | number | Date): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return `${two(date.getDate())} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`
}

export function formatTime(value: string | number | Date, seconds = false): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return `${two(date.getHours())}:${two(date.getMinutes())}${seconds ? `:${two(date.getSeconds())}` : ''}`
}

export function formatDateTime(value: string | number | Date): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return `${formatDate(date)} ${formatTime(date)}`
}
