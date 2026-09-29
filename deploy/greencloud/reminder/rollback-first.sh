#!/bin/sh
# Retire only Step 6 assets. Reminder data and backups remain recoverable.
set -eu
umask 077

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

test "$(id -u)" = 0
state=/srv/snotes/reminder-deploy-step06
test -d "$state"
backup_dir=$(sed -n '1p' "$state/backup-dir")
case "$backup_dir" in
    /root/greencloud-backups/step06-*) ;;
    *) printf '%s\n' 'invalid Step 6 backup directory state' >&2; exit 64 ;;
esac
test -d "$backup_dir"
retired=$backup_dir/rollback-$(date -u +%Y%m%dT%H%M%SZ)
test ! -e "$retired"
install -d -o root -g root -m 0700 "$retired"

systemctl disable --now snotes-reminder-api.service snotes-reminder-scheduler.timer snotes-reminder-backup.timer || true
systemctl stop snotes-reminder-scheduler.service snotes-reminder-backup.service snotes-reminder-api.service || true

# Remove only the marked site block. Never restore the old whole Caddyfile over
# unrelated edits made after Step 6.
caddy_current=/etc/caddy/Caddyfile
test -f "$caddy_current"
caddy_candidate=$(mktemp /etc/caddy/.Caddyfile.rollback-step06.XXXXXX)
auth_candidate=
trap 'rm -f "$caddy_candidate" "${auth_candidate-}"' EXIT HUP INT TERM
awk '
    $0 == "# BEGIN S-NOTES STEP06 REMINDER" {
        if (inside || starts != 0) exit 70
        inside = 1
        starts += 1
        next
    }
    $0 == "# END S-NOTES STEP06 REMINDER" {
        if (!inside || ends != 0) exit 71
        inside = 0
        ends += 1
        next
    }
    !inside { print }
    END { if (inside || starts != 1 || ends != 1) exit 72 }
' "$caddy_current" > "$caddy_candidate"
chown --reference="$caddy_current" "$caddy_candidate"
chmod --reference="$caddy_current" "$caddy_candidate"
caddy_bin=$(command -v caddy)
"$caddy_bin" validate --config "$caddy_candidate" --adapter caddyfile
mv "$caddy_candidate" "$caddy_current"
"$caddy_bin" validate --config "$caddy_current" --adapter caddyfile
systemctl reload caddy

# Remove only the exact two lines appended for the dedicated backup key.
authorized_keys=/var/lib/snotes-publish/.ssh/authorized_keys
addition=$state/authorized-keys-addition
test -f "$authorized_keys"
test -f "$addition"
while IFS= read -r line; do
    grep -Fqx "$line" "$authorized_keys"
done < "$addition"
auth_candidate=$(mktemp /var/lib/snotes-publish/.ssh/.authorized_keys.rollback-step06.XXXXXX)
awk 'NR == FNR { drop[$0] = 1; next } !($0 in drop)' "$addition" "$authorized_keys" > "$auth_candidate"
chown --reference="$authorized_keys" "$auth_candidate"
chmod --reference="$authorized_keys" "$auth_candidate"
mv "$auth_candidate" "$authorized_keys"
auth_candidate=

# Revoke the dedicated ntfy identity without restoring its database over later
# ntfy changes. The credential files were prepared separately and are retained.
/srv/ntfy/app/ntfy user --config /srv/ntfy/deploy/server.yml remove snotes-reminder-staging

for target in \
    /usr/local/sbin/snotes-reminder-backup-ssh-entry \
    /usr/local/sbin/snotes-reminder-backup-export \
    /etc/sudoers.d/snotes-reminder-backup \
    /etc/systemd/system/snotes-reminder-api.service \
    /etc/systemd/system/snotes-reminder-scheduler.service \
    /etc/systemd/system/snotes-reminder-scheduler.timer \
    /etc/systemd/system/snotes-reminder-backup.service \
    /etc/systemd/system/snotes-reminder-backup.timer
do
    if [ -e "$target" ]; then
        install -d -o root -g root -m 0700 "$retired$(dirname "$target")"
        mv "$target" "$retired$target"
    fi
done
systemctl daemon-reload

printf 'STEP06_RETIRED=%s\n' "$retired"
printf '%s\n' 'STEP06_ROLLBACK=PASS'
