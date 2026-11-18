import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import indexHtml from '../../index.html?raw'
import manifestJson from '../../public/manifest.webmanifest?raw'
import { BRAND, BrandMark } from './BrandMark'

afterEach(cleanup)

// every file in public/brand, keyed by path (text read for the SVGs; the PNGs only need to exist)
const brandFiles = import.meta.glob('../../public/brand/*', { query: '?raw', import: 'default', eager: true }) as Record<string, string>
const brandNames = Object.keys(brandFiles).map(path => path.split('/').pop())

describe('final TRINETRA logo', () => {
  it('uses the supplied SVG lockup by default and the mark alone when space is tight, always with the alt text TRINETRA', () => {
    const { rerender } = render(<BrandMark/>)
    expect((screen.getByAltText('TRINETRA') as HTMLImageElement).getAttribute('src')).toBe('/brand/trinetra-logo-dark.svg')
    rerender(<BrandMark compact/>)
    expect((screen.getByAltText('TRINETRA') as HTMLImageElement).getAttribute('src')).toBe('/brand/trinetra-mark-dark.svg')
    rerender(<BrandMark surface="light"/>)
    expect((screen.getByAltText('TRINETRA') as HTMLImageElement).getAttribute('src')).toBe('/brand/trinetra-logo-light.svg')
  })

  it('every file the interface refers to exists in public/brand, and the page head points at the final favicon, icons and manifest', () => {
    for (const file of [...Object.values(BRAND.lockup), ...Object.values(BRAND.mark), '/brand/favicon.svg', '/brand/favicon-32.png', '/brand/app-icon-192.png', '/brand/app-icon-512.png', '/brand/apple-touch-icon.png']) {
      expect(brandNames).toContain(file.split('/').pop())
    }
    expect(indexHtml).toContain('href="/brand/favicon.svg"')
    expect(indexHtml).toContain('href="/brand/apple-touch-icon.png"')
    expect(indexHtml).toContain('href="/manifest.webmanifest"')
    const manifest = JSON.parse(manifestJson) as { icons: Array<{ src: string }>; name: string }
    expect(manifest.name).toBe('TRINETRA')
    expect(manifest.icons.map(icon => icon.src)).toEqual(expect.arrayContaining(['/brand/app-icon-192.png', '/brand/app-icon-512.png']))
  })

  it('the logo files are still real SVG artwork titled TRINETRA', () => {
    for (const name of ['trinetra-logo-dark.svg', 'trinetra-logo-light.svg', 'trinetra-mark-dark.svg', 'trinetra-mark-light.svg']) {
      const svg = brandFiles[`../../public/brand/${name}`]
      expect(svg.startsWith('<svg')).toBe(true)
      expect(svg).toContain('<title>TRINETRA')
    }
  })
})
