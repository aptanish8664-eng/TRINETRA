import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DetectorStatus } from './DetectorStatus'

describe('DetectorStatus', () => {
  it('labels the default heuristic and abstract simulation boundary', () => {
    render(<DetectorStatus detector={{ mode: 'heuristic', model_id: null }} />)

    expect(screen.getByText('Trajectory residual heuristic')).toBeTruthy()
    expect(screen.getByText(/No trained model is active/)).toBeTruthy()
    expect(screen.getByText(/not PX4 or field telemetry/)).toBeTruthy()
  })

  it('renders the selected backend model output and uncalibrated confidence', () => {
    render(<DetectorStatus
      detector={{ mode: 'trained_ml', model_id: 'model-id-1234567890' }}
      detection={{
        attack_probability: 0.846, confidence: 0.846, confidence_is_calibrated: false,
        detected: true, classification: 'gps_anomaly', threat_severity: 85,
        threat_level: 'critical', model_id: 'model-id-1234567890',
      }}
    />)

    expect(screen.getByText(/Research classifier · abstract simulation/)).toBeTruthy()
    expect(screen.getByText('84.6%')).toBeTruthy()
    expect(screen.getByText(/uncalibrated/)).toBeTruthy()
    expect(screen.getByText('85/100 · CRITICAL')).toBeTruthy()
    expect(screen.getByText(/not flight telemetry/)).toBeTruthy()
  })

  it('does not guess a detector when old replay state lacks provenance', () => {
    render(<DetectorStatus />)
    expect(screen.getByText('Metadata unavailable')).toBeTruthy()
  })
})
