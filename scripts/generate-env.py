#!/usr/bin/env python3
"""Interactive wizard that generates the .env file and rendered edge/ assets
(Caddyfile + login/authorize pages) that `docker compose up -d` reads.

Run scripts/generate-grants.py first - you'll need the ai_readonly password
it prints out.

Usage (interactive):
    python3 generate-env.py

Usage (non-interactive):
    python3 generate-env.py \\
        --addressing-mode ip --address 203.0.113.10 \\
        --db-host 127.0.0.1 --db-port 3306 --db-name mydb \\
        --db-user ai_readonly --db-password '<from generate-grants.py>' \\
        --auth-password 'MyChosenPassword123!' \\
        --app-name "My App DB Gateway"
"""
import argparse
import getpass
import os
import secrets
import string
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(ROOT, "edge", "pages")
CADDY_TEMPLATE = os.path.join(ROOT, "edge", "Caddyfile.template")
RENDERED_DIR = os.path.join(ROOT, "edge", "rendered")

DEFAULT_APP_NAME = "MySQL MCP Gateway"
DEFAULT_DESCRIPTION = "Authorize this connection to query the database (read-only)."
DEFAULT_ACCENT = "#6366f1"
DEFAULT_ACCENT_2 = "#8b5cf6"
DEFAULT_TRUSTED_PROXIES = "172.16.0.0/12"  # covers Docker's default bridge network ranges


def generate_token(nbytes=32):
    return secrets.token_hex(nbytes)


def generate_strong_password(length=20):
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_="
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in pwd)
            and any(c.isupper() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and any(c in "!@#$%^&*-_=" for c in pwd)
        ):
            return pwd


def prompt(label, default=None, secret=False):
    suffix = f" [{default}]" if default else ""
    reader = getpass.getpass if secret else input
    val = reader(f"{label}{suffix}: ").strip()
    return val or default


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--addressing-mode", choices=["ip", "domain"])
    p.add_argument("--address", help="The bare IP or the domain name")
    p.add_argument("--db-host", default="127.0.0.1")
    p.add_argument("--db-port", type=int, default=3306)
    p.add_argument("--db-name")
    p.add_argument("--db-user", default="ai_readonly")
    p.add_argument("--db-password")
    p.add_argument("--auth-password", help="Login password for the gateway; auto-generated if omitted")
    p.add_argument("--mcp-secret", help="Shared bearer secret between edge/authproxy and mcp; auto-generated if omitted")
    p.add_argument("--app-name", default=DEFAULT_APP_NAME)
    p.add_argument("--description", default=DEFAULT_DESCRIPTION)
    p.add_argument("--accent-color", default=DEFAULT_ACCENT)
    p.add_argument("--accent-color-2", default=DEFAULT_ACCENT_2)
    p.add_argument("--trusted-proxies", default=DEFAULT_TRUSTED_PROXIES)
    return p.parse_args()


def render_template(path, substitutions):
    with open(path) as f:
        content = f.read()
    for key, value in substitutions.items():
        content = content.replace(f"{{{{{key}}}}}", value)
    return content


def main():
    args = parse_args()

    addressing_mode = args.addressing_mode or prompt("Addressing mode: ip or domain", "ip")
    if addressing_mode not in ("ip", "domain"):
        print("Addressing mode must be 'ip' or 'domain'", file=sys.stderr)
        sys.exit(1)

    address = args.address or prompt(
        "Public IP address" if addressing_mode == "ip" else "Domain (must already point at this server)"
    )
    if not address:
        print("An IP or domain is required.", file=sys.stderr)
        sys.exit(1)

    db_host = args.db_host or prompt("MySQL host", "127.0.0.1")
    db_port = args.db_port or int(prompt("MySQL port", "3306"))
    db_name = args.db_name or prompt("Database/schema name")
    db_user = args.db_user or prompt("Gateway MySQL username", "ai_readonly")
    db_password = args.db_password or prompt(
        "Password for that MySQL user (printed by generate-grants.py)", secret=True
    )

    auth_password = args.auth_password or generate_strong_password()
    mcp_secret = args.mcp_secret or generate_token()

    app_name = args.app_name
    description = args.description
    accent_color = args.accent_color
    accent_color_2 = args.accent_color_2
    trusted_proxies = args.trusted_proxies

    # --- .env ---
    env_lines = [
        f"ADDRESSING_MODE={addressing_mode}",
        f"SITE_ADDRESS={address}",
        "",
        f"MYSQL_HOST={db_host}",
        f"MYSQL_PORT={db_port}",
        f"MYSQL_USER={db_user}",
        f"MYSQL_PASS={db_password}",
        f"MYSQL_DB={db_name}",
        "",
        "IS_REMOTE_MCP=true",
        "ALLOW_INSERT_OPERATION=false",
        "ALLOW_UPDATE_OPERATION=false",
        "ALLOW_DELETE_OPERATION=false",
        "MCP_PORT=3000",
        "",
        f"AUTH_PASSWORD={auth_password}",
        f"REMOTE_SECRET_KEY={mcp_secret}",
        f"EXTERNAL_URL=https://{address}",
        f"TRUSTED_PROXIES={trusted_proxies}",
        "AUTHPROXY_PORT=8080",
        "",
        f"APP_NAME={app_name}",
    ]
    with open(os.path.join(ROOT, ".env"), "w") as f:
        f.write("\n".join(env_lines) + "\n")

    # --- edge/rendered/Caddyfile ---
    os.makedirs(RENDERED_DIR, exist_ok=True)
    if addressing_mode == "domain":
        site_address = address
        tls_directive = ""  # Caddy auto-manages ACME for a real domain
    else:
        site_address = f"https://{address}"
        tls_directive = "\ttls internal"

    caddyfile = render_template(
        CADDY_TEMPLATE,
        {"SITE_ADDRESS": site_address, "TLS_DIRECTIVE": tls_directive},
    )
    with open(os.path.join(RENDERED_DIR, "Caddyfile"), "w") as f:
        f.write(caddyfile)

    # --- edge/rendered/login.html + authorize.html ---
    page_subs = {
        "APP_NAME": app_name,
        "DESCRIPTION": description,
        "ACCENT_COLOR": accent_color,
        "ACCENT_COLOR_2": accent_color_2,
    }
    for name in ("login.html", "authorize.html"):
        rendered = render_template(os.path.join(TEMPLATES_DIR, f"{name}.tmpl"), page_subs)
        with open(os.path.join(RENDERED_DIR, name), "w") as f:
            f.write(rendered)

    print(f"\nWrote .env and edge/rendered/ (Caddyfile, login.html, authorize.html).")
    print(f"Login password: {auth_password}")
    print("Save this - it's shown only once. Next: docker compose up -d")


if __name__ == "__main__":
    main()
