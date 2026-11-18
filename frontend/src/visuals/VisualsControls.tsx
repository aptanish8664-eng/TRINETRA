import { useVisuals } from './useVisuals'

/** "Lite visuals" (still poster image instead of video, no animation; remembered). */
export function VisualsControls() {
  const { lite, setLite } = useVisuals()
  return <div className="visuals-controls" role="group" aria-label="Visual settings">
    <button type="button" aria-pressed={lite} onClick={() => setLite(!lite)} title="Shows a still picture instead of the background video and turns every decorative animation off">LITE VISUALS {lite ? 'ON' : 'OFF'}</button>
  </div>
}
