# mysql-mcp-gateway

Give Claude (or ChatGPT, or Codex) safe, read-only access to your production
MySQL database — with specific columns like `password` permanently hidden,
no matter how the question is phrased.

```
AI client  --OAuth-->  edge (Caddy, TLS)  -->  authproxy (mcp-auth-proxy)  -->  mcp (mcp-server-mysql)  -->  MySQL
```

One `docker compose up -d` gets you a working, OAuth-gated MCP endpoint that
plugs straight into Claude Desktop, Claude Code, ChatGPT, and Codex CLI as a
"custom connector" — none of which accept a bare API key for this kind of
integration; they all expect a URL and a real OAuth handshake.

## Why this exists

Claude's (and ChatGPT's) custom connector UI only takes a URL plus an
optional OAuth Client ID/Secret — never a static bearer token. Getting a
self-hosted MySQL MCP server to actually satisfy that, with TLS, on a box
that's usually *already running your real app*, took real trial and error to
get right the first time: a silently-defaulting transport mode, a
path-doubling 404, a redirect loop, a password-policy rejection, a
CIDR-format panic. This project bakes all of those fixes in so you don't
have to rediscover them.

A close alternative, [`neverinfamous/mysql-mcp`](https://github.com/neverinfamous/mysql-mcp),
bundles its own OAuth into the MCP server and ships Docker too — but assumes
a reverse proxy already fronts it and has no TLS/certbot story. This project
is specifically for the "I already have nginx/Caddy/an app running on this
box, and I want the login/authorize pages to look like mine, and I want to
be able to switch from a bare IP to a real domain later without redoing
everything" scenario.

## Quickstart

Requires Docker and Docker Compose. Requires your MySQL server to be
reachable from wherever you run this (same box or over the network).

```bash
git clone <this-repo> mysql-mcp-gateway
cd mysql-mcp-gateway

# 1. Create the read-only database user (see full walkthrough below)
python3 scripts/generate-grants.py
mysql -u root -p < grants.generated.sql

# 2. Configure the gateway
python3 scripts/generate-env.py

# 3. Bring it up
docker compose up -d
```

That's it — you now have an OAuth-gated MCP endpoint at
`https://<your-ip-or-domain>/mcp`. See **Connecting a client** below.

## Create the read-only database user

`scripts/generate-grants.py` automates this (it never executes anything
against your database — it only introspects table/column names, then writes
a `grants.generated.sql` file for you to review and run yourself). This
section is the manual version, both so you can run it by hand instead of
trusting a script against production, and to explain the security model.

**1. Create the user**, with a password meeting MySQL 8's default
`validate_password` STRONG policy (12+ characters, upper and lower case, a
digit, a special character — a weak password here will simply be rejected):

```sql
CREATE USER 'ai_readonly'@'%' IDENTIFIED WITH caching_sha2_password BY '<a strong password>';
```

**2. Grant access, table by table.** For a table with nothing sensitive in
it, a plain grant is fine:

```sql
GRANT SELECT ON your_db.orders TO 'ai_readonly'@'%';
```

For any table containing a column you want to keep hidden — most commonly
`users.password` — grant access to specific columns instead of the whole
table:

```sql
GRANT SELECT (id, email, name, created_at) ON your_db.users TO 'ai_readonly'@'%';
-- note: password is simply not in this list.
```

**This has to be done table by table, not layered.** A schema-wide or
table-level `GRANT SELECT` silently overrides any narrower column-level
grant sitting underneath it — so if you ever also run
`GRANT SELECT ON your_db.* TO 'ai_readonly'@'%';`, that one statement grants
full access to every column in every table, `password` included, regardless
of any column-level grant you set up earlier. There's no "layering" of
grants from broad to narrow; the broadest one wins.

**3. What's deliberately absent:** no `INSERT`, `UPDATE`, `DELETE`, `DROP`,
or `ALTER` grants anywhere, on anything. This user can only ever read.

**Worked example.** Given a small schema:

```sql
CREATE TABLE users (id INT, email VARCHAR(255), name VARCHAR(255), password VARCHAR(255));
CREATE TABLE orders (id INT, user_id INT, total DECIMAL(10,2));
```

The generated grants would be:

```sql
CREATE USER 'ai_readonly'@'%' IDENTIFIED WITH caching_sha2_password BY '<generated>';
GRANT SELECT (id, email, name) ON your_db.users TO 'ai_readonly'@'%';
GRANT SELECT ON your_db.orders TO 'ai_readonly'@'%';
FLUSH PRIVILEGES;
```

Now `ai_readonly` can answer "how many orders has each user placed" but a
request for `SELECT password FROM users` — however it's phrased — fails at
the database itself, not by trusting the AI to behave.

## Connecting a client

Once `docker compose up -d` is running, point any of these at
`https://<your-ip-or-domain>/mcp`:

- **Claude Desktop** — Settings → Connectors → Add custom connector → paste
  the URL, leave OAuth Client ID/Secret blank, log in once with the password
  `generate-env.py` printed.
- **Claude Code (CLI)** — `claude mcp add mysql-gateway --transport http
  https://<your-ip-or-domain>/mcp -H "Authorization: Bearer <token>"`.
- **ChatGPT app** — Settings → Connectors → Advanced → Developer Mode
  (Pro/Team/Enterprise/Edu) → add the same URL. Requires a genuinely public
  HTTPS endpoint (domain mode, or IP mode with a real cert).
- **Codex CLI** — add to `~/.codex/config.toml`:
  ```toml
  [mcp_servers.mysql-gateway]
  url = "https://<your-ip-or-domain>/mcp"
  auth = "oauth"
  ```
  then `codex mcp login mysql-gateway`.

## Rotating secrets

```bash
python3 scripts/rotate-secrets.py --auth-password       # new login password, auto-generated
python3 scripts/rotate-secrets.py --mcp-secret           # new shared bearer secret
python3 scripts/rotate-secrets.py --auth-password 'MyChosenPassword123!'
```

Neither touches MySQL or the grants you set up.

## How it works

Three containers, one `docker-compose.yml`:

- **`mcp`** — [`@benborla29/mcp-server-mysql`](https://github.com/benborla/mcp-server-mysql),
  running in remote HTTP mode. Talks to your database with the read-only
  user above. Never exposed outside the Docker network.
- **`authproxy`** — [`sigbit/mcp-auth-proxy`](https://github.com/sigbit/mcp-auth-proxy),
  a real OAuth 2.1 authorization server (metadata discovery, dynamic client
  registration, authorization code + PKCE). Validates the login, then
  attaches a shared bearer secret when forwarding requests to `mcp` — so
  `mcp` itself never has to know anything about OAuth. Also never exposed
  outside the Docker network.
- **`edge`** — Caddy. The only container that's actually reachable from the
  internet. Serves the branded login/authorize pages for `GET` requests,
  transparently reverse-proxies everything else (including every `POST`,
  and `/mcp` itself) to `authproxy` unchanged, and owns TLS — a self-signed
  cert for a bare IP, or automatic Let's Encrypt for a real domain, decided
  entirely by what `scripts/generate-env.py` writes into
  `edge/rendered/Caddyfile`.

## Status

This is the initial (Phase 1) release: standalone installs, IP or domain
addressing, secret rotation, and all four client connection paths. Domain
switching after the fact, deeper branding customization, running alongside
an existing reverse proxy, and a bare-metal/no-Docker path are on the
roadmap — see the plan this was built from for the full phased breakdown.

## License

MIT — see [LICENSE](LICENSE).
