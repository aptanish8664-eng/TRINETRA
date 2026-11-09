import type { DetectorInfo, MLDetection } from '../types'

export function DetectorStatus({ detector, detection }: { detector?: DetectorInfo; detection?: MLDetection | null }) {
  if (!detector) {
    return <section className="detector-status panel" aria-label="Detector status">
      <div><span className="eyebrow">DETECTOR</span><strong>Metadata unavailable</strong></div>
      <p>This stored state does not identify its detector configuration.</p>
    </section>
  }

  if (detector.mode === 'heuristic') {
    return <section className="detector-status panel" aria-label="Detector status">
      <div><span className="eyebrow">ACTIVE DETECTOR</span><strong>Trajectory residual heuristic</strong></div>
      <p>Threshold calibrated on synthetic clean-run noise. No trained model is active.</p>
      <small>Abstract software simulation · not PX4 or field telemetry</small>
    </section>
  }

  return <section className="detector-status panel" aria-label="Detector status">
    <div><span className="eyebrow">ACTIVE DETECTOR</span><strong>Research classifier · abstract simulation</strong></div>
    <p>Model <code title={detector.model_id ?? undefined}>{detector.model_id?.slice(0, 12) ?? 'ID unavailable'}</code></p>
    {!detection ? <p>Waiting for the selected aircraft’s first simulation step.</p> : <div className="detector-prediction">
      <span>ATTACK PROBABILITY <b>{(detection.attack_probability * 100).toFixed(1)}%</b></span>
      <span>CONFIDENCE <b>{(detection.confidence * 100).toFixed(1)}% · {detection.confidence_is_calibrated ? 'calibrated' : 'uncalibrated'}</b></span>
      <span>THREAT <b>{detection.threat_severity}/100 · {detection.threat_level.toUpperCase()}</b></span>
      <span>CLASS <b>{detection.classification.replace(/_/g, ' ').toUpperCase()}</b></span>
    </div>}
    <small>Research output from abstract simulator data · confidence is not calibrated · not flight telemetry</small>
  </section>
}
