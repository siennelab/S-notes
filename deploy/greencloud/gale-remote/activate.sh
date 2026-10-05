#!/bin/sh
# Activate only after independent Auth0 configuration and credential provisioning.
set -eu
test "$(id -u)" = 0
test "$(hostname)" = optimal-living
test -s /root/greencloud-secrets/gale-remote-oauth.json
! grep -Fqx '# BEGIN S-NOTES GALE REMOTE MCP' /etc/caddy/Caddyfile
systemctl enable --now snotes-gale-remote-mcp.service
# Fail closed: do not publish until the authenticated app has OAuth discovery.
/srv/snotes/runtime/.venv/bin/python - <<'PY'
import json
import time
import urllib.request
for attempt in range(15):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8768/.well-known/oauth-protected-resource/mcp', timeout=3) as response:
            metadata = json.load(response)
        assert metadata['resource'] == 'https://snotes-gale.ke-den.de/mcp'
        break
    except Exception:
        if attempt == 14:
            raise SystemExit('local_oauth_discovery_failed')
        time.sleep(1)
PY
backup_dir=$(mktemp -d /root/greencloud-backups/gale-remote-XXXXXXXX)
chmod 0700 "$backup_dir"
cp -p /etc/caddy/Caddyfile "$backup_dir/Caddyfile.before"
candidate=$(mktemp /etc/caddy/.gale-remote-XXXXXXXX)
trap 'rm -f "$candidate"' EXIT
cp --preserve=mode,ownership,timestamps /etc/caddy/Caddyfile "$candidate"
cat /srv/snotes/gale-remote/deploy/gale.Caddyfile >> "$candidate"
cmp -n "$(stat -c %s /etc/caddy/Caddyfile)" /etc/caddy/Caddyfile "$candidate"
caddy validate --config "$candidate" --adapter caddyfile
mv "$candidate" /etc/caddy/Caddyfile
systemctl reload caddy
systemctl is-active --quiet snotes-gale-remote-mcp.service caddy
printf 'GALE_REMOTE_ACTIVATED=PASS\nCADDY_RECOVERY=%s\n' "$backup_dir"
