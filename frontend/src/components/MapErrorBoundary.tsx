import { Component, type ReactNode } from 'react'

type Props = { label?: string; children: ReactNode }
type State = { error: Error | null; attempt: number }

/** Isolates a map failure: the surrounding form (and its unsaved state) keeps working and the map can be retried. */
export class MapErrorBoundary extends Component<Props, State> {
  state: State = { error: null, attempt: 0 }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error) {
    console.error('Map component failed', error)
  }

  render() {
    if (this.state.error) {
      return <div className="cesium-map-error-boundary" role="alert">
        <b>{this.props.label ?? 'The map'} failed to render.</b>
        <span>{this.state.error.message || 'Unknown map error.'} The rest of this page is unaffected and nothing you entered was lost.</span>
        <button type="button" className="button-secondary" onClick={() => this.setState(state => ({ error: null, attempt: state.attempt + 1 }))}>RETRY MAP</button>
      </div>
    }
    return <div key={this.state.attempt} className="cesium-map-boundary-frame">{this.props.children}</div>
  }
}
