#!/bin/sh
# Retire this first deployment, keeping its assets and last mirrors recoverable.
set -eu
test "$(id -u)" = 0
umask 077
retired="/srv/snotes/retired-step05-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 0700 "$retired"
for target in /var/lib/snotes-publish/.ssh/authorized_keys /etc/sudoers.d/snotes-mirror-receiver /usr/local/sbin/snotes-mirror-ssh-entry /usr/local/sbin/snotes-mirror-receiver /usr/local/bin/snotes-cloud-read; do
    if test -e "$target"; then
        mkdir -p "$retired$(dirname "$target")"
        mv "$target" "$retired$target"
    fi
done
pkill -TERM -u 2001,2002 -f '^/srv/snotes/runtime/.venv/bin/python -B -m src.cloud_read_mcp$' || true
# Runtime and accounts stay in place, disabled by the removed launch/SSH entries.
# No previous S-notes deployment existed; mirrors remain available for recovery.
printf 'STEP05_RETIRED=%s\n' "$retired"
