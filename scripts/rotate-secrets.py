#!/usr/bin/env python3
"""Rotate the gateway's login password and/or the shared MCP bearer secret,
without touching MySQL or re-running the full setup wizard.

Usage:
    python3 rotate-secrets.py --auth-password                  # auto-generate
    python3 rotate-secrets.py --auth-password 'MyNewPass123!'  # custom value
    python3 rotate-secrets.py --mcp-secret                     # auto-generate
    python3 rotate-secrets.py --auth-password --mcp-secret     # both, auto-generated
"""
import argparse
import os
import secrets
import string
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")


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


def generate_token(nbytes=32):
    return secrets.token_hex(nbytes)


def is_weak(password):
    return (
        len(password) < 12
        or not any(c.islower() for c in password)
        or not any(c.isupper() for c in password)
        or not any(c.isdigit() for c in password)
    )


def read_env():
    if not os.path.exists(ENV_PATH):
        print(f".env not found at {ENV_PATH} - run scripts/generate-env.py first.", file=sys.stderr)
        sys.exit(1)
    with open(ENV_PATH) as f:
        return f.readlines()


def set_env_var(lines, key, value):
    """Replace a KEY=value line in place, preserving everything else."""
    prefix = f"{key}="
    replaced = False
    new_lines = []
    for line in lines:
        if line.startswith(prefix):
            new_lines.append(f"{prefix}{value}\n")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"{prefix}{value}\n")
    return new_lines


def docker_compose_up(*services):
    cmd = ["docker", "compose", "up", "-d", *services]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print("docker compose up failed - check the output above.", file=sys.stderr)
        sys.exit(1)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--auth-password",
        nargs="?",
        const="__generate__",
        default=None,
        metavar="VALUE",
        help="Rotate the login password. Pass a value, or omit the value to auto-generate one.",
    )
    p.add_argument(
        "--mcp-secret",
        nargs="?",
        const="__generate__",
        default=None,
        metavar="VALUE",
        help="Rotate the shared MCP bearer secret. Pass a value, or omit to auto-generate.",
    )
    return p.parse_args()


def main():
    args = parse_args()
    if args.auth_password is None and args.mcp_secret is None:
        print("Nothing to rotate - pass --auth-password and/or --mcp-secret.", file=sys.stderr)
        sys.exit(1)

    lines = read_env()
    services_to_restart = set()

    if args.auth_password is not None:
        value = args.auth_password
        if value == "__generate__":
            value = generate_strong_password()
        elif is_weak(value):
            print(
                "Warning: that password doesn't look strong (12+ chars, upper, lower, digit "
                "recommended). Proceeding anyway since you provided it explicitly.",
                file=sys.stderr,
            )
        lines = set_env_var(lines, "AUTH_PASSWORD", value)
        services_to_restart.add("authproxy")
        print(f"New login password: {value}")

    if args.mcp_secret is not None:
        value = args.mcp_secret
        if value == "__generate__":
            value = generate_token()
        lines = set_env_var(lines, "REMOTE_SECRET_KEY", value)
        # Both mcp and authproxy must agree on this value: mcp checks it on
        # incoming requests, authproxy attaches it when forwarding. Restarting
        # only one would break auth between them.
        services_to_restart.update({"mcp", "authproxy"})
        print(f"New MCP shared secret: {value}")

    with open(ENV_PATH, "w") as f:
        f.writelines(lines)

    docker_compose_up(*sorted(services_to_restart))
    print("\nDone. The database connection and MySQL grants were not touched.")


if __name__ == "__main__":
    main()
