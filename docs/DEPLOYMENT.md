# Deployment preparation

Status: configuration is present but no Netlify site, backend host, Supabase project, or remote simulator has been deployed or smoke-tested.

The target is three separately managed responsibilities: a static React frontend on Netlify, a long-running FastAPI/WebSocket backend on a container-capable host, and PostgreSQL on Supabase. PX4/Gazebo stays in the local WSL development environment until a separate remote simulator host is designed and measured. Deploying this web stack does not deploy or connect a PX4 simulator.

## Netlify frontend

`netlify.toml` sets the base to `frontend`, runs a locked npm installation and Vite production build, publishes `dist`, and rewrites app routes to `index.html` for the existing history-based SPA. The build deliberately fails unless `VITE_API_URL` is configured.

In Netlify site build environment variables, set:

- `VITE_API_URL` to the absolute HTTPS origin of the FastAPI service, without a path, for example `https://api.example.com`. It is embedded in the public frontend bundle, so it must never contain a credential or secret. The browser uses the same setting to derive the WebSocket URL (`wss://` for HTTPS).

Set the frontend and API on HTTPS custom hostnames sharing the same registrable domain, for example `console.example.com` and `api.example.com`. TRINETRA session cookies are `Secure`, `HttpOnly`, `SameSite=Strict`, and scoped to `/api`; unrelated `netlify.app` and backend-host domains are cross-site and will not reliably support this cookie flow. Verify cookie behavior in the selected host/browser combination before inviting users.

The backend environment must set `TRINETRA_ALLOWED_ORIGINS` to the exact frontend origin, such as `https://console.example.com`, with no wildcard. Configure the same value for HTTP CORS and WebSocket origin checks. The API host must accept HTTPS and WebSocket upgrades and keep long-lived WebSocket connections open; Netlify only serves the frontend in this deployment design, while the browser connects directly to the API origin.

## FastAPI container

Build from the repository root so Docker can read `backend/requirements.txt`:

```powershell
docker build -f backend/Dockerfile -t trinetra-backend .
```

The image installs `libpq5` for the Psycopg 3 runtime, copies the FastAPI app and Alembic migrations, runs as a non-root `trinetra` user, honors the host-provided `PORT`, and defines an HTTP health check at `/api/health`. The endpoint probes the database and reports in-process simulator/WebSocket, detector, and AI-key configuration; it explicitly reports PX4 and telemetry as not integrated. It does not probe external simulator processes, provider reachability, or individual telemetry freshness.

Set these backend-host environment variables through its secret/environment manager:

- `DATABASE_URL`: PostgreSQL SQLAlchemy URL using Psycopg 3. Keep it private and server-side.
- `TRINETRA_ALLOWED_ORIGINS`: exact deployed frontend origin.
- `TRINETRA_SESSION_COOKIE_SECURE=true`.
- `PORT`: normally assigned by the platform; the image defaults to `8000` for local use.
- `OPENAI_API_KEY`: optional server-side secret if live AI is intentionally enabled.
- `OPENAI_MODEL`: optional server-side model selection.

Before starting a new empty PostgreSQL database, run the container's `python -m alembic upgrade head` as a one-off release/migration command with the same `DATABASE_URL`. Do not run this on an existing database without a reviewed, restorable backup and schema comparison. An existing database with application tables but no `alembic_version` requires a deliberate baseline adoption plan; never stamp it blindly. Keep migrations backward-compatible with the application version used during rollback.

Run one backend process/instance for this implementation. Active simulation objects and login/registration throttling are process-local; horizontal scaling, restarts during active runs, and scale-to-zero do not currently provide a shared simulation runtime. PostgreSQL preserves persisted records but does not make in-memory active simulations distributed.

The container defaults to SQLite if `DATABASE_URL` is missing, but container-local SQLite storage is ephemeral and unsuitable as a production persistence target. Set PostgreSQL explicitly on the host. Do not copy `backend/trinetra.db`, backups, test data, `.env`, or credentials into the image; `.dockerignore` excludes those paths and the Dockerfile copies only runtime source and migration files.

## Deployment acceptance and rollback

Before enabling a public deployment, verify the Netlify production build with `VITE_API_URL`, frontend deep links, HTTPS login/session/CSRF flow, WebSocket handshake and sustained connection, exact CORS/WebSocket origins, health response, PostgreSQL persistence across backend restart, secret redaction, and a documented backup/restore drill. No such hosted verification has been performed yet.

For rollback, redeploy the previous frontend and backend artifacts only when their schema compatibility is known. Do not automatically reverse migrations or restore a database backup over newer data. Record the deployed app revision, applied Alembic revision, backup time/location, restoration decision owner, and observed health before resuming traffic.

## Platform references

- [Netlify Vite deployment](https://docs.netlify.com/build/frameworks/framework-setup-guides/vite/)
- [Netlify file-based build configuration](https://docs.netlify.com/build/configure-builds/file-based-configuration/)
- [Netlify rewrites and proxies](https://docs.netlify.com/manage/routing/redirects/rewrites-proxies/)
- [Netlify single-page application routing](https://docs.netlify.com/resources/troubleshooting/page-not-found-error-guide/)
- [Docker multi-stage build concepts](https://docs.docker.com/get-started/docker-concepts/building-images/multi-stage-builds/)
- [MDN Set-Cookie and SameSite behavior](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie)

