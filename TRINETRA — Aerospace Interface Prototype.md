# TRINETRA — Aerospace Interface Prototype

A responsive React/Vite frontend prototype for a cyber-resilient multi-UAV research simulator. This is a standalone visual-design project created for handoff and later integration. It does not modify or represent the existing TRINETRA repository.

## Run locally

Requirements: Node.js 20+ and pnpm 10.18.0 (the package manager is pinned in `package.json`).

```bash
pnpm install
pnpm dev:static
```

The Vite development server listens on port 3000 by default. To choose a different port for local preview, set `PORT` when starting it, for example `PORT=3000 pnpm dev:static`. The managed project preview uses the configured port.

To run the available checks and build a static distribution:

```bash
pnpm check
pnpm build:static
```

The build output is written to `dist/public/`.

## Landing experience

The landing page is an interactive five-scene scroll narrative: system introduction, simulated spoofing detection, threat explanation, fleet-task reassignment, and a command-center preview with a direct Mission Control entry. Framer Motion links the scene transitions and technical SVG visuals to page-scroll progress; wheel, trackpad, and touch input remain native. Reduced-motion preferences switch to a static, vertically readable sequence. All signals, GPS differences, routes, and task changes are illustrative only.

## Screens

- `/` — cinematic landing page and product story
- `/auth` — login and account-request interface states
- `/mission-control` — abstract simulation canvas, illustrative status and event timeline
- `/mission-planner` — scenario configuration, route visualization and simulated planning suggestion
- `/mission-assessment` — assumptions, constraints, validation findings and clearly pending approval state
- `/threat-assessment` — synthetic evidence, severity, affected units and review-only response controls
- `/fleet` — selectable illustrative unit roster
- `/analytics` — clearly labeled synthetic charts and experiment comparisons
- `/mission-history` — illustrative replay timeline
- `/scenario-lab` — local demonstration controls and synthetic sample results
- `/admin` — sample users, local-only registration decisions and illustrative audit records

All operational routes share consistent navigation and visible prototype disclosures. The website route manifest is at `client/public/manus-routes.json`.

## Dependencies

The prototype uses the initialized project dependencies without adding packages: React 19, TypeScript, Vite, Wouter, Framer Motion, Recharts, and Lucide React. CSS and inline SVG provide abstract route graphics, radar sweeps, waypoint transitions, and responsive styling. There is no Three.js scene, backend, database, or real AI service.

## Landing hero asset

The landing visual is self-contained: CSS builds the atmospheric aerial field, and inline SVG supplies the illustrative UAV silhouette and route overlays. `Home.tsx` also keeps an optional project-managed `/manus-storage/` image reference behind that fallback; the preview remains complete if the remote asset does not resolve. When moving this source to another host, either keep the fallback or transfer the image into that host’s public asset storage and update the `HERO` path.

## Data and safety boundaries

All shown fleet names, events, telemetry, charts, conditions, user records, clearances, and metrics are invented demonstration examples. They are labeled as illustrative/synthetic. The simulator canvas uses abstract nodes only, not real geographic coordinates. Local controls change presentation state only. Login does not authenticate; account requests are not submitted; approval, threat response, administrator decisions, scenario results, and history do not connect to external systems. Animations do not signify a real mission approval or execution.

## Handoff

The implementation is organized in `client/src/App.tsx`, `client/src/pages/`, `client/src/components/`, `client/src/data/`, and `client/src/index.css`. Integrate through review of the source tree; no existing TRINETRA repository was changed. `plan.md` records the approved approach, and `TODO.md` tracks the requested outcomes.
