# One local Compose application

Run these commands from the `qa-platform` backend directory. Keep the sibling
`frontend` directory beside it: Compose builds the frontend there.

```powershell
docker compose config --quiet
docker compose pull --ignore-buildable
docker compose up -d --build --wait --wait-timeout 180
docker compose ps -a
```

Docker Desktop groups everything under **qa-platform**. Containers are created
from images by Docker; images are not pulled inside a parent container.

| Service | Purpose | Local address |
| --- | --- | --- |
| frontend | Built React app served by Nginx, with API proxy | http://localhost:3000 |
| api | FastAPI | http://localhost:8000/docs |
| worker | arq discovery jobs, Node/MCP and Chrome | Internal |
| postgres | Versioned application data | localhost:5432 |
| redis | Job queue/results, persisted with AOF | localhost:6379 |
| minio | S3-compatible evidence storage | http://localhost:9001 console |
| migrate | Runs Alembic before API/worker start | Exits successfully |
| storage-init | Creates the evidence bucket idempotently | Exits successfully |

The two setup containers normally show **Exited (0)**. This is expected, not a
failure. PostgreSQL and Redis must be healthy and migrations complete before
application startup. Frontend waits for API database readiness.

The old command `docker compose -f deploy/docker/docker-compose.yml ...` includes
the same canonical `compose.yaml`; it does not create a second application.
Use Compose v2.24+ (or newer). Do not add `container_name`: Compose supplies unique
names and service DNS automatically.

## Configuration and persistence

Copy `.env.example` to `.env` only if no `.env` exists. Backend provider keys and
`CREDENTIAL_ENCRYPTION_KEY` stay in that uncommitted file and are passed only to
backend services. Configure an LLM provider for extraction/generation and a valid
Fernet key for encrypted target login credentials. Retain that encryption key
across restarts so saved credentials remain readable.

Compose overrides host database/Redis/storage addresses with internal service
names. The Python entrypoint constructs the database URL from POSTGRES variables,
including correct escaping of special characters in passwords. Changing
POSTGRES_PASSWORD after the volume was initialized does not change PostgreSQL's
stored password; update the database account deliberately in that case.

Ports bind to localhost and can be changed with the port variables in `.env`.
Local-development credentials are defaults, not production deployment settings.
The frontend image does not copy `.env` files and uses the same-origin `/api/v1`
proxy. Nginx permits long synchronous extraction/generation requests.

Persistent volumes are `qa-platform_pgdata`, `qa-platform_redis-data`, and
`qa-platform_minio-data`. Unrelated existing applications and volumes are not
modified or migrated into this stack.

```powershell
docker compose stop              # stop services, keep containers and data
docker compose start             # start existing containers
docker compose down              # remove this stack's containers/network, keep data
docker compose logs --tail 100 api worker
```

Do not add `--volumes` to down unless you intentionally want to erase saved data.

## Browser worker and image sources

The worker installs Node, Chrome DevTools MCP, and Chrome at image build time;
discovery does not download npm packages per job. Its MCP command/browser path
are explicit, while local non-Docker usage retains npx. Chrome runs headless as
an unprivileged container user; its browser sandbox is disabled only in this
local Docker setup to avoid user-namespace restrictions. The worker has a 1 GiB
shared-memory allocation and defaults to one queued discovery job at a time.
Each discovery job uses its own bounded browser pool (three workers by default,
up to five); set `worker_limit` in the discovery request to tune parallel paths.
Its job timeout exceeds the default 900-second crawl budget.

Object storage is [RustFS](https://github.com/rustfs/rustfs) (`rustfs/rustfs`).
Community MinIO images no longer allow anonymous pulls, and MinIO AIStor
denies S3 operations unless a license is installed. RustFS keeps the same
local endpoint (`http://minio:9000`), console port 9001, and root credentials.
Bucket creation still uses the AIStor `mc` client (`quay.io/minio/aistor/mc`),
which remains anonymously pullable.
Browser installation follows [Playwright's browser documentation](https://playwright.dev/python/docs/browsers),
and MCP flags follow its [configuration guide](https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/main/docs/configuration.md).
This is a development stack; pin verified digests and review image maintenance
before any production deployment.

## Troubleshooting

- Docker engine unavailable: start Docker Desktop with Linux containers enabled.
- Port occupied: stop the conflicting local process or change its port variable.
- Migrations failed: inspect `docker compose logs migrate`; API/worker remain stopped.
- Worker unhealthy: inspect worker logs and Redis health.
- Requirement extraction returns 502: check the configured LLM provider/key/model.
- Credentials fail to save: configure a valid, stable CREDENTIAL_ENCRYPTION_KEY.
- Target runs on your Windows host: use `host.docker.internal` instead of
  `localhost` in its application URL. Container localhost refers to itself.
