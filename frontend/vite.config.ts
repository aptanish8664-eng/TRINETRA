import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { viteStaticCopy } from 'vite-plugin-static-copy'

const cesiumSource = 'node_modules/cesium/Build/Cesium'
const cesiumBaseUrl = 'cesiumStatic'

export default defineConfig({
  define: { CESIUM_BASE_URL: JSON.stringify(`/${cesiumBaseUrl}/`) },
  plugins: [
    tailwindcss(),
    react(),
    viteStaticCopy({
      targets: ['Workers', 'ThirdParty', 'Assets', 'Widgets'].map(directory => ({
        src: `${cesiumSource}/${directory}/**/*`,
        dest: `${cesiumBaseUrl}/${directory}`,
        // The glob is rooted at the Vite project, so its preserved path
        // includes node_modules/cesium/Build/Cesium/<directory>. Strip those
        // five segments to match Cesium's runtime-relative asset URLs.
        rename: { stripBase: 5 },
      })),
    }),
  ],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
  build: {
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/cesium/') || id.includes('/node_modules/@cesium/')) return 'cesium'
          if (id.includes('/node_modules/three/')) return 'three'
          if (id.includes('/node_modules/gsap/')) return 'gsap'
          if (id.includes('/node_modules/framer-motion/')) return 'motion'
          if (id.includes('/node_modules/react/') || id.includes('/node_modules/react-dom/')) return 'react-vendor'
        },
      },
    },
  },
})
