# mysql-mcp-gateway — open-source MySQL read-only MCP + OAuth gateway

## Context

We hand-built a working setup for a client (twentytimestwo) that lets Claude Desktop/Code query
their production MySQL database read-only — with the `users.password` column permanently
excluded — gated behind real OAuth 2.1 so it plugs into Claude's "custom connector" UI (which only
accepts a URL + optional OAuth Client ID/Secret, never a raw bearer token). Getting it working took
real debugging: six distinct, non-obvious bugs (npm cache ownership under a service account,
a silently-defaulting transport mode, a path-doubling 404, an nginx trailing-slash redirect loop,
a MySQL password-policy rejection, and a CIDR-format panic), plus two rough edges the client
noticed afterward: the default OAuth login/consent pages are bare unstyled HTML, and switching a
box from "just an IP" to "a real domain" later meant redoing DNS/cert/nginx/proxy wiring by hand.
That's the value worth packaging: nobody should have to rediscover any of this the hard way.

The user wants this turned into a standalone open-source project — `mysql-mcp-gateway` — that any
IT person running a MySQL-backed app can install to get the same setup. A close competitor
(`neverinfamous/mysql-mcp`) already exists (OAuth 2.1 + Docker bundled into the MCP server itself)
but assumes a reverse proxy already fronts it, has no TLS/certbot story, and doesn't offer branded
auth pages or an IP/domain switch — the gaps we fill.

**Decisions locked in:**
- Name `mysql-mcp-gateway` (avoids the `mysql-mcp` name collision on npm/GitHub). Published to the
  user's own GitHub as `svfox001/mysql-mcp` — no actual collision there since it's namespaced under
  their account, just a naming note.
- Docker Compose as the primary install path; bare-metal/systemd docs as a secondary path.
- MIT license.
- We orchestrate two existing OSS projects (`@benborla29/mcp-server-mysql`,
  `sigbit/mcp-auth-proxy`) rather than reimplementing either — but we add our own thin **edge**
  layer in front of them to get customizable branding and easy IP/domain switching, since neither
  upstream project supports that.

## Architecture: a Caddy `edge` layer

For the client, TLS/branding was handled by hand-rolled nginx + certbot + an nginx `sub_filter`
hack to reskin the bare OAuth pages — fragile (couples to mcp-auth-proxy's exact HTML) and manual
to switch domains. For this project, a purpose-built **Caddy** service (`edge`) solves three
requirements at once:

1. **Customizable OAuth/Authorize pages.** `edge` serves our *own* pre-rendered branded HTML for
   `GET /.auth/login` and `GET /.idp/auth/*` (templated at setup time from branding values — app
   name, one-line description, accent color), while transparently reverse-proxying the `POST` on
   those same paths (and everything else, including `/mcp`) through to `authproxy` unchanged. This
   only swaps what's *rendered*, never the actual auth logic — robust against upstream markup
   changes, unlike CSS-injection.
2. **IP mode vs domain mode.** Caddy's `tls internal` directive gives a trivial self-signed-cert
   path for bare-IP installs; a real domain gets Caddy's normal automatic Let's Encrypt with zero
   certbot/renewal-hook plumbing. Both are just different one-line site addresses in the same
   Caddyfile.
3. **Easy IP → domain switch later.** A `switch-mode` step (Phase 2, not yet built) regenerates the
   Caddyfile's site address block and updates `authproxy`'s `--external-url`, then restarts just
   `edge` and `authproxy` — `mcp` and the MySQL grants stay untouched, no DNS/cert/nginx rework.

This also gives a clean answer for "already has an existing app on the box" installs (Phase 4, not
yet built): `edge` just binds an internal port instead of owning 443, and the existing
nginx/Caddy/Apache gets one reverse-proxy snippet pointed at it.

## Repo layout

```
mysql-mcp-gateway/
├── README.md                       # quickstart, positioning, architecture, full MySQL user
│                                    # walkthrough
├── LICENSE                         # MIT
├── PLAN.md                         # this file
├── docker-compose.yml              # mcp + authproxy + edge (one file, modes differ by config)
├── env.example                     # documents every var (named without the leading dot - a
│                                    # file-write guard blocks anything matching .env.*)
├── docker/
│   ├── mcp.Dockerfile               # node:20-alpine + npm install -g @benborla29/mcp-server-mysql
│   └── authproxy.Dockerfile         # alpine + pinned mcp-auth-proxy release binary download
├── scripts/
│   ├── generate-grants.py          # column-exclusion SQL generator - DONE
│   ├── generate-env.py             # interactive wizard -> .env + rendered edge/ assets - DONE
│   ├── switch-mode.py              # IP <-> domain switch - NOT YET BUILT (Phase 2)
│   ├── rotate-secrets.py           # rotate auth login password / MCP shared secret - DONE
│   └── verify.sh                   # end-to-end OAuth flow smoke test - DONE, not yet run live
├── edge/
│   ├── Caddyfile.template          # site block placeholders - DONE
│   └── pages/
│       ├── login.html.tmpl         # DONE
│       └── authorize.html.tmpl     # DONE
├── examples/
│   └── existing-proxy-snippet.conf # NOT YET BUILT (Phase 4)
├── systemd/                        # bare-metal alternative path - NOT YET BUILT (Phase 5)
│   ├── mysql-mcp-gateway-mcp.service
│   ├── mysql-mcp-gateway-auth.service
│   └── mysql-mcp-gateway-edge.service
└── docs/
    ├── ARCHITECTURE.md             # NOT YET BUILT
    ├── INSTALL.md                  # NOT YET BUILT (README covers quickstart for now)
    ├── BRANDING.md                 # NOT YET BUILT (Phase 3)
    ├── SWITCHING-MODES.md          # NOT YET BUILT (Phase 2)
    ├── ROTATING-SECRETS.md         # NOT YET BUILT (README covers the basics for now)
    ├── CONNECTING-CLIENTS.md       # DONE
    ├── INSTALL-BARE-METAL.md       # NOT YET BUILT (Phase 5)
    └── TROUBLESHOOTING.md          # NOT YET BUILT (Phase 6) - the six original gotchas
```

## What Phase 1 actually shipped (current state)

Standalone installs (edge owns the public port directly, no existing reverse proxy assumed), IP or
domain addressing, secret rotation, and connection docs for all four clients (Claude Desktop, Claude
Code, ChatGPT app, Codex CLI). `README.md` includes the full manual "create the read-only MySQL
user" walkthrough (not just the automated script), explaining why column-level grants have to be
set table-by-table and never layered under a broader `SELECT`.

Two known gaps from this pass, worth closing early in the next session:
- **Docker Desktop's engine wasn't running** when this was built, so `docker compose config` was
  validated (syntax/variable interpolation all correct) but a real `build` / `up` /
  `scripts/verify.sh` run was never done. Do that first, on an actual box, before trusting this.
- `docker/*.Dockerfile` aren't in the original plan's file tree - they were a necessary addition
  since neither upstream project publishes a confirmed registry image. `mcp.Dockerfile` does
  `npm install -g @benborla29/mcp-server-mysql` at build time; `authproxy.Dockerfile` downloads the
  pinned `v2.10.2` release binary directly from GitHub releases (the same binary already validated
  end-to-end in the client engagement this project is based on).

## Component notes for what's still unbuilt

**`scripts/switch-mode.py` (Phase 2).** Re-run after initial install to move IP → domain (or change
the domain). Re-prompts only for the new addressing info, regenerates the Caddyfile site-address
block and `authproxy`'s `EXTERNAL_URL` in `.env`, then runs `docker compose up -d edge authproxy` -
`mcp` and the database grants are never touched. Should reuse `generate-env.py`'s existing
`render_template` function rather than duplicating it.

**Branding (Phase 3).** The templating mechanism already exists and works (`generate-env.py`
already accepts `--app-name`, `--description`, `--accent-color`, `--accent-color-2` and renders
them into the pages right now) - what's actually missing for Phase 3 is just: promoting these from
CLI flags to interactive wizard prompts with clear defaults, a `branding.yml` file so branding can
be edited without re-running the whole wizard, and `docs/BRANDING.md` explaining it. No new
rendering logic needed.

**Behind-existing-proxy mode (Phase 4).** `edge` needs a mode where it doesn't publish 443/80 at
all (bind an internal-only port instead), plus `examples/existing-proxy-snippet.conf` showing how
to point an existing nginx/Caddy at that internal port. Watch for the same class of bug hit during
the client engagement: a naively-scoped *prefix* location for the auth pages can intercept and
mis-redirect the bare `/.idp/auth` kickoff URL. If the snippet is nginx, use a regex location
scoped to paths that already have a session ID segment (`location ~ ^/\.idp/auth/.+ { ... }`), not
a bare prefix location - that was the actual root cause of a redirect-loop bug in the client build,
confirmed by comparing backend-direct vs through-nginx behavior. Caddy's `edge` config itself
already avoids this class of bug by construction (its `@matcher`/`handle` blocks don't have
nginx's implicit directory-redirect behavior), so this only matters for the example snippet aimed
at *other* reverse proxies sitting in front of `edge`.

**Bare-metal/systemd (Phase 5).** Reuse the same `.env` values `generate-env.py` already produces;
just need three systemd unit files templating them in instead of docker-compose, matching the
pattern already proven in the client engagement (in particular: give the `mcp` service its own
writable `HOME`, e.g. `/var/lib/mysql-mcp-gateway`, if running it via `npx` directly on a host
instead of in a container - that's the npm-cache-ownership gotcha).

**Polish (Phase 6).** `docs/TROUBLESHOOTING.md` writing up the six gotchas explicitly (content
already exists in this plan's history, just needs transcribing), CONTRIBUTING.md, CI that lints the
compose file, Caddyfile template, and shell/py scripts.

## Verification checklist for whoever continues this

1. `docker compose build && docker compose up -d` on a real box - first real end-to-end test.
2. `scripts/verify.sh <ip-or-domain>` - should PASS with the `mysql_query` tool listed and
   `readOnlyHint: true`.
3. Load the login/authorize pages in a real browser, confirm the branded template renders.
4. Once `switch-mode.py` exists: run it IP → a real test domain, re-run `verify.sh` against the new
   domain, confirm `mcp` and the MySQL grants needed zero changes.
5. `rotate-secrets.py --auth-password` with a custom value, confirm the old password stops working
   and the new one works; separately rotate `--mcp-secret`, confirm `verify.sh` still passes
   end-to-end afterward (this rotation restarts both `mcp` and `authproxy`, since both must agree
   on the shared secret simultaneously).
6. Follow `docs/CONNECTING-CLIENTS.md` literally, for real, in all four clients - this is also where
   the ChatGPT app / Codex CLI steps (confirmed via research, not yet hands-on tested against this
   actual gateway) get corrected against reality if anything's off.

## Scope calls made without re-asking (flag if wrong)

- Branding v1 is name + one-line description + accent color only - no logo upload pipeline, to
  keep the template renderer simple. Easy to extend later.
- Standalone-mode default auth is the built-in `--password` mode (zero external account needed);
  OIDC is documented as opt-in but not yet built into the wizard.
- Compose references the two upstream images indirectly by building small Dockerfiles from their
  npm package / pinned release binary, rather than publishing our own prebuilt image to a registry
  - revisit once Phase 1 is proven live.
