# Builds mcp-auth-proxy from its published release binary rather than the
# upstream repo's own Dockerfile, so the exact binary we've validated end-to-end
# is what gets shipped. Bump MCP_AUTH_PROXY_VERSION to pick up new releases.
FROM alpine:3.20

ARG MCP_AUTH_PROXY_VERSION=v2.10.2

RUN apk add --no-cache ca-certificates curl \
    && curl -sL -o /usr/local/bin/mcp-auth-proxy \
       "https://github.com/sigbit/mcp-auth-proxy/releases/download/${MCP_AUTH_PROXY_VERSION}/mcp-auth-proxy-linux-amd64" \
    && chmod +x /usr/local/bin/mcp-auth-proxy

ENTRYPOINT ["/usr/local/bin/mcp-auth-proxy"]
