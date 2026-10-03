# CoRE Stack MCP

FastAPI server that exposes the CoRE Stack public APIs to an MCP client, and writes every call to Postgres.

Cursor (or any other MCP client) connects to this server. The server calls `https://geoserver.core-stack.org` with the caller's API key. Postgres records who called, which tool they used, and whether CoRE Stack accepted the request. The API key itself is not stored.

```text
Cursor  --X-API-Key, X-Client-Name-->  this server  --X-API-Key-->  CoRE Stack /api/v2/
                                              |
                                              +--> Postgres  access_logs, clients
```

## What you need

- Docker, or Python 3.11+ and a Postgres 16 database
- A CoRE Stack API key. Create one at [dashboard.core-stack.org](https://dashboard.core-stack.org/)

## Run with Docker

From this directory:

```bash
docker compose up --build
```

That starts Postgres and the MCP server.

| Service | Address | Login |
| --- | --- | --- |
| MCP | http://127.0.0.1:8080/mcp | CoRE Stack API key in `X-API-Key` |
| Health | http://127.0.0.1:8080/health | none |
| Postgres | `localhost:5432` | user `corestack`, password `corestack`, database `corestack_mcp` |

Check that Postgres is up:

```bash
curl http://127.0.0.1:8080/health
```

A healthy server answers `{"status":"ok"}`.

To stamp a shared upstream key or open the log reader, export these before `docker compose up`:

```bash
export CORE_STACK_API_KEY="your-key"
export ADMIN_TOKEN="choose-a-long-token"
docker compose up --build
```

Prefer a key on each MCP client. `CORE_STACK_API_KEY` is only used when a request arrives without `X-API-Key`.

## Run without Docker

Create the database, then:

```bash
createdb corestack_mcp
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Tables are created on startup. Edit `.env` if Postgres is not on `localhost:5432` with the user and password in `.env.example`.

## Deploy

Use a Linux server with Docker, a public IP, and a DNS name that points at that IP. Caddy obtains a TLS certificate for that name and proxies `https://<your-host>/mcp` to the app. Postgres stays on the Docker network and is not published to the internet.

1. Copy this repository onto the server.
2. Create the env file and replace every `change-me` and the hostname:

```bash
cp deploy/.env.example deploy/.env
```

`deploy/.env` needs:

| Variable | What to set |
| --- | --- |
| `MCP_HOST` | The DNS name, such as `mcp.core-stack.org`. No `https://` and no path |
| `POSTGRES_PASSWORD` | A long password. Avoid `@`, `:`, `/`, and `?` because it is placed in the database URL |
| `ADMIN_TOKEN` | A long token for reading the access log |
| `CORE_STACK_API_KEY` | Leave empty. Each Cursor user sends their own key |

3. Confirm the DNS record answers with this server's IP, then start the stack from the repository root:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
```

4. Check the public health URL:

```bash
curl https://mcp.core-stack.org/health
```

A healthy server answers `{"status":"ok"}`. Caddy can take a minute to issue the certificate on the first start.

5. Point Cursor at the public MCP URL:

```json
{
  "mcpServers": {
    "corestack": {
      "url": "https://mcp.core-stack.org/mcp",
      "headers": {
        "X-API-Key": "<corestack-api-key>",
        "X-Client-Name": "your-name"
      }
    }
  }
}
```

`MCP_HOST` is also the host allowlist. If it does not match the name in the browser or in Cursor, the MCP endpoint answers `421 Misdirected Request` and the reason is only in the container log. Read it with:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs mcp
```

The access log records the caller IP from `X-Forwarded-For` because this stack sets `TRUST_PROXY=true`. Read it with the admin token:

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" https://mcp.core-stack.org/clients
```

To ship a new version, copy the new code to the server and run the same `docker compose up -d --build` command. Postgres data stays in the `pgdata` volume. Tables are created on startup; existing rows are kept.

Do not use the root `docker compose up` command on a public server. That file publishes Postgres on port 5432 with the password `corestack` and turns the host check off.

## Connect from Cursor

Add this to `~/.cursor/mcp.json` and restart Cursor:

```json
{
  "mcpServers": {
    "corestack": {
      "url": "http://127.0.0.1:8080/mcp",
      "headers": {
        "X-API-Key": "<corestack-api-key>",
        "X-Client-Name": "your-name"
      }
    }
  }
}
```

`X-Client-Name` is the name written in the access log. Give each person or agent their own name when they share one API key.

Do not put the API key in a tool argument. The server reads it from the header and forwards it to CoRE Stack.

## How an agent should call data

1. `list_public_apis` to see the route ids.
2. `describe_public_api` with one id, for example `get_tehsil_data`.
3. `call_public_api` with that id and a flat `params` object.

Place names must be the State, District, and Tehsil that CoRE Stack uses. Get them from `get_active_locations`, or from `get_admin_details_by_latlon` when you start with a coordinate.

Example: drought rows for one tehsil.

```json
{
  "api_id": "get_tehsil_data",
  "params": {
    "state": "Uttar Pradesh",
    "district": "Balrampur",
    "tehsil": "Tulsipur",
    "data": "drought"
  }
}
```

Omit `data`, or pass `data=all`, to receive every dataset CoRE Stack generated for that tehsil. A tehsil file may contain only some of the datasets. Pass `fields` on `get_mws_data` and `get_mws_kyl_indicators`, for example `et,runoff`.

## Routes

| api_id | Parameters | What it returns |
| --- | --- | --- |
| `get_active_locations` | `state`, `district`, `tehsil` (all optional) | States, districts, and tehsils that already have CoRE Stack data |
| `get_admin_details_by_latlon` | `latitude`, `longitude` | State, District, and Tehsil names for a point |
| `get_mwsid_by_latlon` | `latitude`, `longitude` | Micro-watershed `uid`, plus State, District, and Tehsil |
| `get_tehsil_data` | `state`, `district`, `tehsil`, `data` | Tehsil datasets, one row per micro-watershed |
| `get_mws_data` | `state`, `district`, `tehsil`, `mws_id`, `fields` | Fortnight evapotranspiration, runoff, and precipitation |
| `get_mws_kyl_indicators` | `state`, `district`, `tehsil`, `mws_id`, `fields` | Know Your Landscape indicators for one micro-watershed |
| `get_generated_layer_urls` | `state`, `district`, `tehsil` | GeoServer layer URLs. Open `layer_url` in QGIS |
| `get_mws_report` | `state`, `district`, `tehsil`, `mws_id` | Watershed report URL |
| `get_mws_geometries` | `state`, `district`, `tehsil` | Micro-watershed polygons |
| `get_village_geometries` | `state`, `district`, `tehsil` | Village polygons |
| `get_waterbodies_data_by_admin` | `state`, `district`, `tehsil` | Waterbodies in the tehsil, with seasonal water, zone of influence, and cropping intensity |
| `get_waterbody_data` | `state`, `district`, `tehsil`, `uid` | One waterbody, same fields as the tehsil list |

The server refuses any path that is not in this list.

## Access log

Every request except `GET /health` inserts one row in `access_logs` and updates `clients`. The same line is written to the process log.

`access_logs` stores:

- time, request id, and duration
- `X-Client-Name`
- SHA-256 fingerprint of the API key, and its last four characters
- source IP, user agent, and `X-Forwarded-For` when `TRUST_PROXY=true`
- HTTP method, path, and status code
- MCP method (`initialize`, `tools/list`, `tools/call`), tool name, and arguments
- outcome: `success`, `client_error`, `upstream_error`, `missing_api_key`, or `error`
- CoRE Stack status code and response size

Argument fields named `api_key`, `token`, `password`, `secret`, or `authorization` are stored as `[redacted]`.

`clients` is one row per API key. A request with no key is grouped by IP, user agent, and client name. Each row has first seen, last seen, request count, and the last tool.

Set `ADMIN_TOKEN`, then:

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" http://127.0.0.1:8080/clients
curl -H "X-Admin-Token: $ADMIN_TOKEN" "http://127.0.0.1:8080/access-logs?limit=50"
```

If `ADMIN_TOKEN` is empty, both routes answer 404.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://corestack:corestack@localhost:5432/corestack_mcp` | Access-log database. Inside Compose the host is `db` |
| `CORE_STACK_BASE_URL` | `https://geoserver.core-stack.org` | CoRE Stack host |
| `CORE_STACK_API_KEY` | empty | Fallback key when the request has no `X-API-Key` |
| `MCP_ALLOWED_HOSTS` | empty | Hostnames this server accepts. Empty means localhost only |
| `ADMIN_TOKEN` | empty | Required to read `/access-logs` and `/clients` |
| `UPSTREAM_TIMEOUT_SECONDS` | `120` | Timeout for one CoRE Stack request |
| `MAX_RESPONSE_CHARS` | `180000` | Tool responses longer than this are cut and marked `truncated` |
| `TRUST_PROXY` | `false` | When `true`, the client IP is the first `X-Forwarded-For` hop |
| `LOG_LEVEL` | `INFO` | Process log level |

Leave `MCP_ALLOWED_HOSTS` empty on your machine. The local Compose file sets it to `*` so a container accepts whatever Host header your browser sends. A public server must set it to the real hostname. See [Deploy](#deploy).

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Tests use a local SQLite file, `.pytest_mcp.db`, and do not need Postgres.

## Layout

```text
app/main.py        FastAPI app, /health, /access-logs, /clients
app/mcp_app.py     MCP tools
app/catalog.py     Allowed CoRE Stack routes
app/corestack.py   Upstream HTTP client
app/middleware.py  Reads the caller and writes the access log
app/models.py             access_logs and clients tables
docker-compose.yml      Postgres and the MCP server, for your own machine
deploy/docker-compose.yml  Public server: Caddy, private Postgres, host allowlist
```
