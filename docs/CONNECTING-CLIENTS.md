# Connecting a client

The gateway itself is client-agnostic — it's just an OAuth-fronted MCP
endpoint at `https://<your-ip-or-domain>/mcp`. Each AI client wires up a
remote MCP connector a little differently. This doc covers all four
currently-supported clients in full detail; the README has the short
version.

## Claude Desktop

1. Open Claude Desktop → Settings → Connectors.
2. Click **Add** → **Add custom connector**.
3. **Name**: anything you like (e.g. "MySQL Gateway").
4. **Remote MCP server URL**: `https://<your-ip-or-domain>/mcp`.
5. Leave **OAuth Client ID** and **OAuth Client Secret** blank — the
   gateway supports dynamic client registration, so Claude registers itself
   automatically.
6. Click **Add**, then **Connect**.
7. A browser window opens showing the gateway's login page. Enter the
   password `scripts/generate-env.py` printed when you ran it.
8. Click **Authorize** on the next screen.

That's a one-time login — Claude Desktop stores the resulting OAuth token
and reuses it silently afterward. If the connector toggle for a given
conversation isn't already on, enable it near the message box before
asking a database question.

## Claude Code (CLI)

Two ways to connect, depending on whether you want the full OAuth flow or a
static token:

**Static bearer token** (the shared secret `generate-env.py` wrote to
`.env` as `REMOTE_SECRET_KEY` — simplest for fully non-interactive setups,
since Claude Code's CLI config accepts a raw `Authorization` header
directly, unlike Desktop/ChatGPT):

```bash
claude mcp add mysql-gateway --transport http \
  https://<your-ip-or-domain>/mcp \
  -H "Authorization: Bearer <REMOTE_SECRET_KEY from .env>"
```

**Full OAuth** (get a token once via the same browser flow as Desktop,
then use it the same way as above) — useful if you'd rather not have the
raw shared secret sitting in a CLI config file.

Verify with `claude mcp list`.

## ChatGPT app

Requires a ChatGPT Pro, Team, Enterprise, or Edu plan, and a genuinely
public HTTPS endpoint — this only works in domain mode, or IP mode with a
real (non-self-signed) certificate. ChatGPT can't reach a server that's
only accessible on localhost or a LAN.

1. Settings → Connectors → Advanced → **Developer Mode**.
2. Add a custom connector, paste the gateway's URL.
3. ChatGPT drives the OAuth authorization-code flow against the gateway
   automatically — same login/authorize pages as Claude Desktop.

## Codex CLI

Supported natively, no proxy needed. Add to `~/.codex/config.toml` (or a
project-local `.codex/config.toml`):

```toml
[mcp_servers.mysql-gateway]
url = "https://<your-ip-or-domain>/mcp"
auth = "oauth"
```

Then run the interactive login once:

```bash
codex mcp login mysql-gateway
```

Credentials are cached after that. Verify with `/mcp` inside a Codex
session.

If you'd rather use a static token instead of the OAuth flow, Codex CLI
also supports `bearer_token_env_var` — point it at an environment variable
holding the `REMOTE_SECRET_KEY` value from `.env`:

```toml
[mcp_servers.mysql-gateway]
url = "https://<your-ip-or-domain>/mcp"
bearer_token_env_var = "MYSQL_GATEWAY_TOKEN"
```

## Sanity-checking the connection, regardless of client

Ask something like *"how many rows are in the `orders` table?"* — the AI
should call the `mysql_query` tool and answer from a real result. Then ask
something like *"show me the password column from users"* — that should
fail or come back empty no matter how it's phrased, since the database
grants (not the AI's judgment) are what actually enforce the restriction.
