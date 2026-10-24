#!/bin/sh
# Prepare only; activation requires independent Auth0 credentials and working DNS.
set -eu
source_root=$(realpath "$1")
test "$(id -u)" = 0
test "$(hostname)" = optimal-living
test "$(id -u claude)" = 2001
test "$(id -g claude)" = 2001
test "$(getent passwd claude | cut -d: -f7)" = /usr/sbin/nologin
test -r /srv/snotes/mirror/claude-code/current.db
test -r /srv/snotes/actor-credentials/claude-code/reminder-token
test -d /srv/snotes/gale-remote/vendor/fastmcp
test ! -e /etc/systemd/system/snotes-claude-web-remote-mcp.service
test ! -e /srv/snotes/runtime/src/claude_web_remote_mcp.py
test ! -e /srv/snotes/runtime/src/snotes/transport/mcp/claude_web_remote_auth.py
# Verified against the actual GreenCloud production baseline before this change.
test "$(sha256sum /srv/snotes/runtime/src/snotes/transport/mcp/greencloud_remote_auth.py | cut -d' ' -f1)" = 287abc5260cd350b79728d9055ed539e83c2c7f3201233b1812517a8a92ac3f4
! ss -lntH | awk '{print $4}' | grep -Eq '(^|:)8770$'
unit_dir=/srv/snotes/claude-web-remote
test ! -e "$unit_dir"
backup_dir=$(mktemp -d /root/greencloud-backups/claude-web-remote-XXXXXXXX)
chmod 0700 "$backup_dir"
# The only existing production source replaced by this addition.
cp -p /srv/snotes/runtime/src/snotes/transport/mcp/greencloud_remote_auth.py "$backup_dir/greencloud_remote_auth.py.before"
install -d -m 0755 "$unit_dir" "$unit_dir/deploy"
install -d -o claude -g claude -m 0700 "$unit_dir/oauth"
for file in src/claude_web_remote_mcp.py \
    src/snotes/transport/mcp/claude_web_remote_auth.py \
    src/snotes/transport/mcp/greencloud_remote_auth.py
do
    install -m 0644 "$source_root/$file" "/srv/snotes/runtime/$file"
done
for file in config.json claude.Caddyfile snotes-claude-web-remote-mcp.service provision_credentials.py verify_oauth.py activate.sh INSTALL.md
do
    install -m 0644 "$source_root/deploy/greencloud/claude-web-remote/$file" "$unit_dir/deploy/$file"
done
install -m 0644 "$unit_dir/deploy/config.json" "$unit_dir/config.json"
install -m 0644 "$unit_dir/deploy/snotes-claude-web-remote-mcp.service" /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/snotes-claude-web-remote-mcp.service
systemctl daemon-reload
printf 'CLAUDE_WEB_PREPARED=PASS\nACTIVATED=NO\nRECOVERY=%s\n' "$backup_dir"
