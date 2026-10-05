#!/bin/sh
# Run as root on GreenCloud with the unpacked source directory as argument.
set -eu
source_root=$(realpath "$1")
test "$(id -u)" = 0
test "$(hostname)" = optimal-living
test -x /usr/local/bin/snotes-gale-internal-mcp
test "$(getent passwd snotes-gale | cut -d: -f7)" = /usr/sbin/nologin
test ! -e /etc/systemd/system/snotes-gale-remote-mcp.service
test ! -e /srv/snotes/runtime/src/greencloud_remote_mcp.py
! ss -lntH | awk '{print $4}' | grep -Eq '(^|:)8768$'
unit_dir=/srv/snotes/gale-remote
install -d -m 0755 "$unit_dir" "$unit_dir/deploy"
install -d -m 0700 /root/greencloud-secrets
install -d -o snotes-gale -g snotes-gale -m 0700 "$unit_dir/oauth"
# The existing Python is unchanged; all new dependencies live only in vendor.
(
    umask 022
    /srv/snotes/runtime/.venv/bin/python -m pip install --disable-pip-version-check --no-compile \
        --target "$unit_dir/vendor" -r "$source_root/requirements-mcp.txt"
)
for file in src/greencloud_remote_mcp.py \
    src/snotes/transport/mcp/greencloud_remote_transport.py \
    src/snotes/transport/mcp/greencloud_remote_auth.py
do
    install -m 0644 "$source_root/$file" "/srv/snotes/runtime/$file"
done
for file in config.json gale.Caddyfile provision_credentials.py snotes-gale-remote-mcp.service INSTALL.md install.sh activate.sh verify_oauth.py
do
    install -m 0644 "$source_root/deploy/greencloud/gale-remote/$file" "$unit_dir/deploy/$file"
done
install -m 0644 "$unit_dir/deploy/config.json" "$unit_dir/config.json"
install -m 0644 "$unit_dir/deploy/snotes-gale-remote-mcp.service" /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/snotes-gale-remote-mcp.service
systemctl daemon-reload
echo GALE_REMOTE_INSTALLED_NEEDS_INDEPENDENT_OAUTH_CREDENTIAL=YES
