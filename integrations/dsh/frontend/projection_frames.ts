/** Presentation wire only. RPNH owns the underlying state and event cursor. */
export function projectionStyle(version: string): 'singles' | 'grouped' | undefined {
  switch (version) {
    case '0.2.2-alpha.2': return 'singles'
    case '0.2.2-rc.1':
    case '0.2.2-rc.5': return 'grouped'
  }
}

export function projectionFrames(version: string, seq: number, values: Record<string, unknown>) {
  const entries = Object.entries(values)
  switch (projectionStyle(version)) {
    case 'grouped': return [{ type: 'projection', seq, changes: entries.map(([key, val]) => ({ key, val })) }]
    case 'singles': return entries.map(([key, value]) => ({ type: 'projection', key, value, seq }))
    default: throw new Error('Unsupported official DSH projection protocol')
  }
}
