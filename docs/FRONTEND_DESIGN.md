# Frontend design work (Job 3: FE-A to FE-D)

Frontend only: no backend behaviour, API or function was changed. Mission Control's structure and map were not touched and no decorative animation runs there. Screenshots are in `docs/screenshots/` (`feb-*` Overview, `fec-*` Login, `fe-*` clicks, `fea2-*` Attack Lab). All were taken with a headless Edge (software rendering) at 1914x826, 1440x900 and 900x900.

## FE-A: audit and alignment

Audit (screenshots of Login, Overview, Quick Mission, Scenario Lab, Attack Lab, Fleet, Analytics, History, Research, Mission Control at 1914x826; only Login, Overview, Quick Mission and Attack Lab were examined closely). Real misalignments found:

| Page | Finding | Fixed? |
|---|---|---|
| Login | two stacked headers and two footers (the app bar plus the portal bar); two columns that did not share a top edge; a heading much larger than anything else | **yes**: one header and one footer, one centred card, smaller heading, diagnostics folded under the card |
| Overview | hero text started at x of about 452 px, the section below at about 476 px, the call to action at about 136 px; the three story cards faded in from 25 % opacity (hard to read) | **yes**: every section sits on one 1180 px container with one left edge; cards are solid surfaces and start at 85 % opacity |
| Attack Lab | experiment cards: the OPEN/DELETE row sat at different heights in one row; "Implement" label larger than its neighbours; the empty message flashed while the list was still loading | **yes**: cards are equal-height flex columns with the action row at the bottom (measured: heights and the gap below the actions are identical in every row), smaller label, skeleton while loading |
| Quick Mission | the page title and the panel title say the same thing twice; inputs, selects and buttons have slightly different heights | **partly**: one control height (40 px) is now enforced inside the labs; the duplicated title was **not** changed |
| Scenario Lab, Fleet, Analytics, History, Research | not examined in detail | **not audited** |
| Mission Control | not touched | - |

Shared tokens and components (`src/design-tokens.css`, `src/design-fixes.css`): spacing scale 4/8/12/16/20/24/32/40/48 px, one page container (1180 px, 24 px gutters), a 12-column grid (`.ui-grid`), one Card (`.ui-card`: same padding, radius, border, actions pinned to the bottom), one section heading, one control height, tier colours (Low, Medium, High, Critical), status colours and surface colours defined once, visible keyboard focus rings everywhere, skeleton and empty-state styles. Adopted on the Overview, Login, Attack Lab and the labs' controls; other pages still use their own CSS.

## FE-B: Overview descent

Sky hero (blue gradient to a pale horizon) with cloud layers drifting at three speeds in seamless CSS loops, haze, horizon, far terrain contour lines and a ground grid; an original quadcopter drawn in SVG hovers with spinning rotors and a gentle sway (rotors are blurred discs scaled by a CSS animation). On scroll one passive listener with one requestAnimationFrame per frame writes CSS variables: the layers move at different speeds (clouds 0.15 to 0.85, terrain 0.7, ground 1.0), a slim altitude readout fixed at the right edge counts 120, 90, 60, 30 and 0 m at 0, 25, 50, 75 and 100 % scroll (measured), a dashed flight path draws itself down the left side with a waypoint marker at each section, and the footer is a landing pad where the drone descends and touches down. A fleet strip shows one card per aircraft (link status, battery, threat tier; existing endpoints, polled every 6 s while the tab is visible). Day and night variants (night: dark gradient, stars, red and green rotor lights) with a remembered toggle; the default follows the system theme. **Lite visuals** (remembered) and reduced motion turn the page static: no layer motion, no sway, no flight path, no readout.

What the screenshots show (plain words):
* 1914x826, scroll 0 %: blue sky with white clouds, a dark text panel on the left ("See the signal. Keep the mission.", two buttons), the drone hovering at the upper right, a LITE VISUALS / DAY pair at the top right, the altitude box at the right edge reading 120 m, the ground grid fading into the page at the bottom of the screen.
* 50 %: the dashed path with dots on its left, the fleet strip (five aircraft cards, all connected, battery 100 %, no assessment), then "One altered coordinate can change the whole picture." and the three story cards, all starting on the same left edge; the readout shows 60 m.
* 100 %: the cards' text, the call to action block, then the landing pad with the drone on it and the readout at 0 m; the dashed path is drawn all the way down.
* 1440x900 and 900x900: the same page; at 900 px the navigation wraps under the logo, the hero panel fills the width, the drone, flight path and readout are hidden on purpose, and the cards stack in one column.
* Night: dark blue sky with stars and lit rotor lights (checked at the top and bottom of the Overview at 1914x826).

## FE-C: Login

The sign-in page does not scroll (page scroll distance 0 at all three sizes). It plays by itself: layered clouds, haze and terrain contours in seamless CSS loops, and the original drone on one requestAnimationFrame loop along a figure-eight (period 36 s, closes exactly, banks into its turns, grows and shrinks for depth). It is behind the card in the middle and in front of the sky layers only on the two outer lobes (never at the card; on screens narrower than 1000 px always behind), so it never covers the form. No scroll listeners. The loop stops when the tab is hidden; Lite visuals or reduced motion show one still frame; night variant included. On a successful sign-in the drone flies up and away (650 ms) before the page changes; a failed sign-in shakes the card and the drone and the message "Invalid username or password" is shown in a red-edged line in the card. The sign-in form keeps its labels, and the portal keeps a "System diagnostics" panel (folded) and the footer.

## FE-D extras

1. **Ctrl+K palette**: opens anywhere after sign-in; filters pages and saved missions by name (every word must match), arrow keys and Enter, Escape closes; selecting a mission opens it in Quick Mission; it only navigates (verified in the browser: Ctrl+K, "attack", Enter opened the Attack Lab and nothing was started).
2. **Skeletons and empty states**: skeletons for the fleet strip, the run records, the experiment list and the palette while loading; empty states with a next action for the fleet strip ("No aircraft are running. Start the simulator from Mission Control") and the experiment list. The existing saved-mission empty text was kept.
3. **Toasts**: one style for success, warning and error (top centre under the header, never over the page buttons, at most three, errors stay 9 s, duplicates are not stacked); used by the Attack Lab messages. Other pages still use their inline messages.
4. **Fleet strip** on the Overview (see FE-B).
5. **Focus rings and accessibility**: a visible 2 px focus ring on every control, labelled palette (combobox/listbox roles), toggles with `aria-pressed`, the altitude readout has a text alternative, decorative scenes are `aria-hidden`. No automated contrast audit was run; text on the sky uses a dark panel.

## CPU cost (main thread of the page, headless Edge with software rendering, 1914x826, 12 s)

| Page | visuals on | Lite visuals |
|---|---|---|
| Overview idle | 26 % of one core (script 1.2 %) | 5.6 % |
| Overview scrolling up and down | 23 % | 7.9 % |
| Login idle | 27 % | 3.5 % |

Software rendering moves the compositing onto the CPU, so a laptop with a GPU will show less; this could not be measured here. The sky is the cost: with Lite visuals the pages are close to idle.

## Not done / not verified

* Scenario Lab, Fleet, Analytics, History and Research were not audited or changed; Quick Mission keeps its duplicated title; toasts are used only in the Attack Lab.
* No automated contrast measurement; no test on a real GPU; no mobile device (900 px width only).
* A very small cursor tilt on the Login page (optional) was not built.
* Tests added: `src/visuals/visuals.test.tsx` (9: Lite toggle remembered and disabling animation, reduced motion, night, login loop pause and Lite still frame, seamless drone path, palette navigation and filtering, toasts) and `RestartDialog.count.test.tsx` (2).


---

# Update: real video replaces the cartoon sky and drone

Frontend only; Mission Control has no video and was not touched. The cartoon sky, clouds and SVG drone (`SkyScene`, `Quadcopter`, `LoginScene`) were deleted.

## The footage and the mapping

`D:\TRINETRA\media` held **one** clip (ignored in `.gitignore`; `git status` shows nothing for it): "[Drone Freestyle] Mountain Landscape With Snow", an FPV drone flight, 24.4 s, 1920x1080, 29.97 fps, H.264 with an audio track (7.9 MB). Frames every 2 s show no logos, text, faces or shaking beyond normal FPV motion; it starts at a snowy summit with clouds, descends a snow and rock slope and ends over a valley with forest. Nothing was rejected.
* **Login** (a real drone in flight): this clip, played by itself and looped.
* **Overview**: the same clip, scrubbed with the scroll (summit and clouds at 0 %, snow slope in the middle, valley at 100 %).
* **Missing**: separate clips for a valley, and for ground-level or landing footage, and a different clip for each page. The bottom of the Overview shows the clip's valley end, not a landing. I did not invent or download footage.

## Web versions (`frontend/public/media`, 28.7 MB in total, committed-size)

`flight-720.mp4` 5.4 MB, `flight-720.webm` 6.5 MB, `flight-1080.mp4` 7.7 MB, `flight-1080.webm` 10.3 MB (H.264 and VP9, no audio, faststart, a keyframe every 10-12 frames so scrubbing is smooth), `poster-summit.jpg`, `poster-valley.jpg`. Each is under 12 MB. ffmpeg came from a portable copy (`imageio-ffmpeg`, installed under `D:\TRINETRA_scratch\pylib`); ffprobe was not available, so the clip was probed with ffmpeg. `docs/MEDIA_CREDITS.md` lists the clip; the source URL, the creator and the exact licence are **not in the file and are left as "fill in"** (the uploader tag says "Free HD videos - no copyright"; the title says Creative Commons). A credits line is in the footer of the Overview and the Login page.

## Behaviour

* **Login**: full-screen video, muted, inline, `preload="metadata"`, two copies crossfaded over 1.2 s at the loop point (no jump), dark gradient overlay, centred card; failed sign-in shakes the card and shows "Invalid username or password"; a successful sign-in fades and zooms the page out for 0.6 s, then the Overview opens. If autoplay is refused the poster stays.
* **Overview**: the clip is fixed behind the page and its position follows the scroll (one `<video>`, never plays by itself, eased, at most about 14 seeks a second). The altitude readout (120 m to 0 m, checked at 0/25/50/75/100 %), the flight path and the waypoint markers are a clean overlay over a dark gradient; every card sits on a solid surface; the layout and grid are unchanged. With only one clip there are no clip-to-clip crossfades.
* **Fallbacks**: the poster image replaces the video when Lite visuals is on, with prefers-reduced-motion, below 800 px wide, when autoplay is blocked, and while the tab is hidden or the element is off-screen the video is paused. At most two videos exist (Login) and one on the Overview. **720p is the default**; 1080p is chosen only on a screen at least 1800 px wide and 3000 device pixels (never on this machine's test sizes).

## CPU (whole test browser, all processes including the video decoder, headless Edge with software rendering, 15 s)

| | video | Lite (poster) |
|---|---|---|
| Login idle | 21-24 % of one core | 8-10 % |
| Overview idle | 11 % | 8-18 % |
| Overview scrolling up and down | 64-85 % (before throttling the seeks 85 %, after 64 %) | 37-45 % |

The video costs roughly 10-15 points on the Login page and 20-40 points only while scrolling the Overview. These numbers are noisy and software-rendered; a laptop with GPU video decoding should be cheaper, but that was not measured. Lite visuals is the way to turn it off.

## Screenshots (plain words; `docs/screenshots/video-*`)

* Overview 1914x826, 0 %: a snowy summit ridge under a cloudy sky, darkened, with the dark hero panel on the left, the Lite button top right and the altitude box at 120 m. 25 and 50 %: the same footage has moved on to a snow slope with scattered rocks; the fleet strip, the "One altered coordinate" panel and the three cards sit on solid dark panels; the dashed flight path and its dots are on the left; the readout shows 90 m and 60 m. 75 %: the call to action block. 100 %: the cards' text, "A real simulation. A live operational view.", the landing pad panel with the clip's blurred valley and ridge behind; the readout shows 0 m.
* Overview 1440x900 and 900x900: the same page; at 900 px the layout stacks in one column and the video is still shown (the poster fallback starts below 800 px).
* Login 1914x826, 1440x900, 900x900: the drone flight (snow slope, rocks, a forest valley on the right) fills the screen under a dark overlay; the sign-in card is centred and readable; the credits line is in the footer; the page does not scroll. Login with autoplay refused: the summit poster fills the screen instead, the card is unchanged.
* Failed sign-in: the card has shaken and the red-edged message "Invalid username or password" is under the button. Successful sign-in: the page is caught fading and zooming out, then the navigation bar appears. Lite on the Overview: the poster replaces the video and the altitude readout and flight path disappear.

## Checks

`tsc` clean; frontend **32 files, 352 tests passed** (a first run right after the build had 2 failures in two slow Mission Control render tests that passed on the next full run and when run alone: the known timing flakiness under load); production build OK (72 s). Tests updated or added: `src/visuals/visuals.test.tsx` now covers the two-video crossfade setup, Lite, reduced motion and narrow screens (poster), autoplay refused (poster), pause while hidden, 720p default and the scrubbed position following the scroll; `App.test.tsx` expects the video backdrop instead of the photograph. Click-through (browser): failed sign-in shake and message, successful sign-in transition, Lite toggle and its memory, Ctrl+K navigation, Lite Overview.

Files changed: `.gitignore`; new `frontend/public/media/*`, `docs/MEDIA_CREDITS.md`, `src/visuals/VideoBackdrop.tsx`; changed `src/visuals/{visuals.css,overview-descent.css,login-scene.css,useDescent.ts,VisualsControls.tsx,visuals.test.tsx}`, `src/App.tsx`, `src/App.test.tsx`, `src/components/AccessPortal.tsx`; deleted `SkyScene.tsx`, `Quadcopter.tsx`, `LoginScene.tsx`. The day/night toggle was removed (there is no night footage). Not done: 1080p was not exercised on any test screen; no GPU measurement; the footage source URL, creator and licence still need to be filled in.


---

# Update 3: final logo, slim Login pane, slow frame-sequence Overview, UAV section

Frontend only. Mission Control components and all backend code are unchanged (`git diff --stat` lists none). Screenshots: `docs/screenshots/v2-*`.

## What changed
* **Logo**: the supplied final artwork (nine files from `brand/trinetra-logo-final.zip`, byte-identical copies in `frontend/public/brand`) replaces the old drawn mark everywhere through one `BrandMark` component: lockup in the header and Login pane and UAV section, mark alone in the footer and hero line. `favicon.svg`, `favicon-32.png`, `apple-touch-icon.png` and a new `manifest.webmanifest` (192 and 512 icons) are linked from `index.html`; `README.md` has a logo header. There is no light theme, so the light files are only wired in (`surface="light"`). The old `public/logo/*` files are no longer referenced and were not deleted. The frontend produces only CSV and JSON exports, so there is no export header to carry a logo.
* **Login**: the big card is gone. A slim dark translucent pane (rgba(5,13,23,.5), 6 px blur, 1 px cyan-tinted hairline, 2 px corners, 400 to 440 px wide) sits 8 % in from the right edge, opposite the summit, vertically centred, with a soft dark vignette behind that side only. It holds the logo, "Identify yourself.", the three steps in one row, underline fields and Sign in. "Need an account? REGISTER" sits just under it. Below 1100 px the pane is centred. The Lite visuals button is removed everywhere; automatic fallbacks stay (reduced motion, data-saver, under 800 px, refused autoplay). No clip with a UAV in flight exists in `media`, so the same scenic clip plays.
* **Overview**: about 8.0 viewport heights long (7.9 at 1440x900, 8.9 at 900x900). The whole 24.4 s clip is an image sequence (194 WebP frames, 8 per second, 1280 px wide, 8.8 MB) drawn on a canvas with a crossfade between neighbouring frames, eased towards the scroll position (0.22 s time constant), loaded nearest-first, poster until ready; reduced motion, data-saver, under 800 px or a load failure show one still per section (summit, slope, valley). Programme strip "RESEARCH PLATFORM | SIMULATION ONLY", numbered sections, 2 px corners, 1 px rules, no glow.
* **Elevation**: `ELEVATION_TOP_M = 5400` and `ELEVATION_BOTTOM_M = 2300` in `src/visuals/elevation.ts`; label ELEVATION, unit ASL, nearest 10 m, thousands separators, caption "scenic elevation, not live telemetry". Swept at 101 scroll positions: 5,400 down to 2,310 m, never 0.
* **UAV section**: `uav-snow-scene.webp` (1920x1080), `uav-cutout.webp` and `.png` in `public/media`. The green grass background was keyed out (green dominance plus a yellowness test, connected-component clean-up, background decontamination, despill, 1 px feather), then the aircraft was placed on a mirrored, blurred and snow-graded frame from the clip with cool-tinted contact shadows and snow haze. Light snow falls only when motion is allowed. The original wording is kept ("Touchdown. Every run in this simulator ends on the ground, and every number on these pages comes from the backend.").
* **British text and dates**: "labelled", "favour", "INITIALISING", "AUTHORISATION", "control centre"; dates from `src/services/format.ts` as `18 NOV 2026 14:05` (24-hour). Applied in `App.tsx` and `AccessPortal.tsx` only; other components still use browser-locale dates and were not touched (several are Mission Control).

## Measured worst-case Login text contrast (98 real footage frames, every 0.25 s plus the last frame, pane 50 % opaque, nothing raised)
Computed from screenshots of the pane with its text hidden, using the brightest pixel behind each text box.

| | 1914x826 | 1440x900 | 900x900 |
|---|---|---|---|
| Headline #eaf4fa | 9.37 | 8.40 | 8.79 |
| Labels and step titles #c3d3df | 6.92 | 6.42 | 6.45 |
| Error text #ffc4c8 | 6.84 | 6.54 | 6.88 |
| Muted captions and placeholders #9fb3c3 | 4.91 | 4.57 | 4.57 |
| Sign in label (#04121c on #55d9ff) | 11.51 | 11.51 | 11.51 |

Sampled, not all 729 video frames. The loop crossfade shows only frames from the same clip.

## Verified
`tsc` exit 0; frontend 33 files, 359 tests passed; production build OK in 1m 13s with one warning (chunk over 600 kB, Cesium, as before). Login: no inner scroll and the Sign in button visible at 1914x826, 1440x826, 1914x700, 1914x640, 1440x640, 1100x640, 900x640 and 1914x600. Failed sign-in: the card swung 19 px side to side (36 frames) and showed "Invalid username or password". Success: mid-transition opacity 0.56 and scale 1.018, then the Overview opened. Autoplay refused: poster, no video elements.

## UAV cut-out: what is left
No green halo. The left propeller is a translucent blue shape with torn edges. The right propeller is a dark motion-blur smear (as in the photograph). The right skid has a light grey fringe where grass blades were, there is one tiny purple speck near the right propeller root, and the far-left foot is clipped (hidden under snow haze). The source is 1024 px wide, so the aircraft is slightly soft at full size.

## Not verified or not done
Contrast was measured in headless Edge only. No GPU or CPU measurement was repeated for the frame sequence. The tab icon itself cannot be seen in headless mode; the favicon link resolves to `/brand/favicon.svg` and the files load. Source URL, creator and licence of the clip and the UAV photo are still "fill in" in `docs/MEDIA_CREDITS.md`. The older notes above in this file about the sky, cloud and Lite toggle are superseded.


---

# Update 4: UAV section flush at the bottom, smooth scrolling

Frontend only; Mission Control and the backend are untouched.

## 1. The UAV section is the last thing on the page
**Cause:** the shared 63 px footer was rendered after the Overview. The fixed scenic backdrop is painted above non-positioned content, so that strip showed mountains instead of a footer, and the UAV section ended 63 px above the document bottom (its top 63 px scrolled out of view). **Fix:** the Overview no longer renders the shared footer; its four lines sit inside the bottom of the UAV picture as small dark chips (other pages keep the normal footer). The section is exactly one screen tall. While it covers the screen the fixed backdrop is hidden (`visibility`), so nothing is composited behind it.

Checked by script at maximum scroll (document height minus viewport height equals the maximum scroll; the section's bottom equals the viewport bottom, which is the document bottom; the section's top is 0 and its height equals the viewport; the element at the bottom pixel is the UAV picture; no footer element):

| size | document | max scroll | section top / bottom / height | before |
|---|---|---|---|---|
| 1914x826 | 6,714 px | 5,888 | 0 / 826 / 826 | section ended 63 px short, top at -63 |
| 1440x900 | 7,195 px | 6,295 | 0 / 900 / 900 | same |
| 900x900 | 8,095 px | 7,195 | 0 / 900 / 900 | same |

Falling snow is now 22 plain elements moved by CSS transform animations (no canvas), running only while motion is allowed, the tab is visible and the picture is on screen, behind a CSS mask that keeps them out of the left 46 % (wide screens) or the top 42 % (narrow screens), which is where the logo and text are. On 900 wide the elevation readout was moved above the footer lines.

## 2. Smooth scrolling: measured, then fixed
Method: Playwright with headless Edge at 1914x826, a per-frame `requestAnimationFrame` timer and a long-task observer, scrolling the whole Overview with real mouse-wheel events (40 px every 40 ms) and with 150 px jumps every 100 ms, results split into top, middle and bottom thirds, all 194 frames loaded first. A Chromium trace located the cost.

**Real causes found, in order of size**
1. Every animation frame wrote `--progress` and `--descent` on the Overview root. Custom properties inherit, so each write re-resolved styles for the whole page (ablation: switching the writes off took the median frame from 33 ms to 16.7 ms and slow frames from 408 to 159 of 491).
2. Frames were decoded on the main thread inside the animation callback (trace: 171 `Decode Image` events, up to 128 ms each, inside my `step` function, 5.1 s in all), because the canvas drew `<img>` elements and `createImageBitmap(img)` also decodes on the main thread.
3. The canvas was redrawn whenever any frame finished loading, and the scroll handler read layout (`getBoundingClientRect`).
4. A scroll-scrubbed card animation, an extra scroll listener and a full-size snow canvas.

**Fixes:** progress is written only to the readout text (when the displayed value changes), a transform on the progress bar and the flight path's clip, never to a custom property; page geometry is cached and measured only on mount, resize and when the Overview resizes; the eased value has a time constant of 0.22 s and cannot move faster than 1.6 progress per second; frames are downloaded as files and decoded with `createImageBitmap(blob)` on a worker, keeping 6 behind and 22 ahead of the reader decoded (about 100 MB) and releasing the rest; the canvas draws only when the frame pair or the blend (24 steps) changes, from the one eased animation frame, and never draws an undecoded image; the UAV picture is decoded early; the card scroll-scrub is removed; the snow is compositor-only; the backdrop gets its own contained layer.

**Results (medians across runs, wheel scrolling; 95th percentile frame time in ms / frames over 20 ms / long tasks over 50 ms)**

| third | before (1 run, software) | after (3 runs, software) | after (3 runs, GPU flags) |
|---|---|---|---|
| top | 33.6 / 62 / 0 | 17.1 / 5 / 0 | 18.0 / 11 / 0 |
| middle | 49.9 / 79 / 0 | 33.3 / 14 / 0 | 34.3 / 37 / 0 |
| bottom | 49.7 / 90 / 0 | 17.1 / 7 / 0 | 20.6 / 15 / 0 |

Stepped scrolling after: 95th percentile 16.9, 17.7, 17.5 ms (software) and 17.6, 18.4, 18.9 ms (GPU flags), with 0 to 7 frames over 20 ms and no long tasks; before it was 33.4, 33.8, 49.1 ms with 4 and 2 long tasks (53 and 55 ms). Worst single frames in wheel mode are still 50 to 150 ms now and then (1 to 3 per third). The main thread in a GPU-flag trace never ran a task over 13.6 ms.

**Target (95th percentile under 20 ms and no long tasks over 50 ms in all three thirds):** met for the top and bottom thirds and for stepped scrolling in all thirds, with no long tasks at all in the median runs. **Not met:** the middle third in wheel mode (median 95th percentile 33 to 34 ms in both configurations). A clean middle-third-only run (729 frames) gave 17.1 ms and 10 slow frames, and the ablations that looked much worse had no plausible cause and were load spikes, so the middle-third figure is mostly noise from other work on this machine; I could not separate it further.

**Caveats:** the machine was busy with other work during every run (overall CPU 81 % at one check, a Python process using about 85 % of a core, WSL running, about 3 GB free memory), so run-to-run spread is large (for example one GPU-flag run had 24 long tasks in the bottom third and the next had none). The "before" figures are a single run and were taken with the old code, which no longer exists, so they cannot be repeated; the same-session ablation is the stronger evidence for cause 1. Everything was measured on the Vite dev server (React development build), not a production preview, because the production API address and origin checks were not set up for a second server. No measurement on a laptop with a real GPU was made; "GPU flags" in headless Edge may still fall back to software.

## Tests
`src/visuals/visuals.test.tsx` now also covers: no CSS custom property writes and no layout reads in the scroll handler, the capped easing step and the readout text, redraw only on change, the bounded decoded-frame window, the backdrop hidden once the UAV section covers the screen, and the snow as plain elements. Final: `tsc` exit 0; frontend 33 files, 361 tests passed (two earlier full runs lost worker processes under memory pressure and were discarded; the third, with three workers, was complete); production build OK in 1m 3s with the same single chunk-size warning (Cesium).
