#!/bin/sh
set -eu
test "$(id -u)" = 0
test "$(hostname)" = optimal-living
test -s /root/greencloud-secrets/claude-web-remote-oauth.json
getent ahostsv4 snotes-claude.ke-den.de | awk '{print $1}' | grep -Fxq 109.94.170.215
! grep -Fqx '# BEGIN S-NOTES CLAUDE WEB REMOTE MCP' /etc/caddy/Caddyfile
systemctl start snotes-claude-web-remote-mcp.service
# Publish only after the actual OAuth provider and fixed runtime start successfully.
if ! /srv/snotes/runtime/.venv/bin/python - <<'PY'
import json
import time
import urllib.request
for attempt in range(15):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8770/.well-known/oauth-protected-resource/mcp', timeout=3) as response:
            metadata = json.load(response)
        assert metadata['resource'] == 'https://snotes-claude.ke-den.de/mcp'
        assert metadata['scopes_supported'] == ['notes']
        break
    except Exception:
        if attempt == 14:
            raise SystemExit('local_oauth_discovery_failed')
        time.sleep(1)
PY
then
    systemctl stop snotes-claude-web-remote-mcp.service
    exit 1
fi
backup_dir=$(mktemp -d /root/greencloud-backups/claude-web-caddy-XXXXXXXX)
chmod 0700 "$backup_dir"
cp -p /etc/caddy/Caddyfile "$backup_dir/Caddyfile.before"
candidate=$(mktemp /etc/caddy/.claude-web-XXXXXXXX)
trap 'rm -f "$candidate"' EXIT
cp --preserve=mode,ownership,timestamps /etc/caddy/Caddyfile "$candidate"
cat /srv/snotes/claude-web-remote/deploy/claude.Caddyfile >> "$candidate"
caddy validate --config "$candidate" --adapter caddyfile
mv "$candidate" /etc/caddy/Caddyfile
systemctl reload caddy
systemctl enable snotes-claude-web-remote-mcp.service
systemctl is-active --quiet snotes-claude-web-remote-mcp.service caddy
printf 'CLAUDE_WEB_ACTIVATED=PASS\nCADDY_RECOVERY=%s\n' "$backup_dir"
