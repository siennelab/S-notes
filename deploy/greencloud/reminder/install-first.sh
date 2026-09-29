#!/bin/sh
# First Step 6 installation only. Run as root from the unpacked package root.
set -eu
umask 077

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

test "$(id -u)" = 0
test "$(id -u snotes)" = 2006
getent passwd snotes-publish >/dev/null
test -x /srv/snotes/runtime/.venv/bin/python
test -f /var/lib/snotes-publish/.ssh/authorized_keys
test "$(tail -c 1 /var/lib/snotes-publish/.ssh/authorized_keys | wc -l)" -eq 1
test -f /etc/caddy/Caddyfile
test -f backup-transport.pub
test -s /root/greencloud-secrets/reminder-api.json
test -s /root/greencloud-secrets/reminder-ntfy-token
test "$(stat -c '%U:%G:%a' /root/greencloud-secrets/reminder-api.json)" = root:root:600
test "$(stat -c '%U:%G:%a' /root/greencloud-secrets/reminder-ntfy-token)" = root:root:600

awk 'NF { n += 1; if ($1 !~ /^ssh-/ || NF < 2) exit 1 } END { exit !(n == 1) }' backup-transport.pub
ssh-keygen -l -f backup-transport.pub >/dev/null
backup_public_key=$(awk 'NF { print; exit }' backup-transport.pub)

for target in \
    /srv/snotes/reminder-runtime \
    /srv/snotes/data/reminder.db \
    /srv/snotes/backups/reminder \
    /srv/snotes/reminder-deploy-step06 \
    /srv/snotes/rollback-step06.sh \
    /usr/local/sbin/snotes-reminder-backup-ssh-entry \
    /usr/local/sbin/snotes-reminder-backup-export \
    /etc/sudoers.d/snotes-reminder-backup \
    /etc/systemd/system/snotes-reminder-api.service \
    /etc/systemd/system/snotes-reminder-scheduler.service \
    /etc/systemd/system/snotes-reminder-scheduler.timer \
    /etc/systemd/system/snotes-reminder-backup.service \
    /etc/systemd/system/snotes-reminder-backup.timer
do
    test ! -e "$target"
done

! grep -Fqx '# snotes-step06-reminder-backup' /var/lib/snotes-publish/.ssh/authorized_keys
! grep -Fq "$backup_public_key" /var/lib/snotes-publish/.ssh/authorized_keys
! grep -Fqx '# BEGIN S-NOTES STEP06 REMINDER' /etc/caddy/Caddyfile
! grep -Fqx '# END S-NOTES STEP06 REMINDER' /etc/caddy/Caddyfile

if [ -n "${STEP06_BACKUP_DIR-}" ]; then
    backup_dir=$STEP06_BACKUP_DIR
    case "$backup_dir" in
        /root/greencloud-backups/step06-*) ;;
        *) printf '%s\n' 'STEP06_BACKUP_DIR must be /root/greencloud-backups/step06-*' >&2; exit 64 ;;
    esac
    test -d "$backup_dir"
else
    backup_dir=/root/greencloud-backups/step06-$(date -u +%Y%m%dT%H%M%SZ)
    test ! -e "$backup_dir"
    install -d -o root -g root -m 0700 "$backup_dir"
fi
install_backup=$backup_dir/reminder-install
test ! -e "$install_backup"
install -d -o root -g root -m 0700 "$install_backup"
cp -p /etc/caddy/Caddyfile "$install_backup/Caddyfile.before"
cp -p /var/lib/snotes-publish/.ssh/authorized_keys "$install_backup/authorized_keys.before"

caddy_candidate=$(mktemp /etc/caddy/.Caddyfile.step06.XXXXXX)
trap 'rm -f "$caddy_candidate"' EXIT HUP INT TERM
cp --preserve=mode,ownership,timestamps /etc/caddy/Caddyfile "$caddy_candidate"
if [ "$(tail -c 1 "$caddy_candidate" | wc -l)" -eq 0 ]; then
    printf '\n' >> "$caddy_candidate"
fi
printf '\n' >> "$caddy_candidate"
cat deploy/greencloud/reminder/reminder.Caddyfile >> "$caddy_candidate"
caddy_bin=$(command -v caddy)
"$caddy_bin" validate --config "$caddy_candidate" --adapter caddyfile
original_caddy_size=$(stat -c %s /etc/caddy/Caddyfile)
cmp -n "$original_caddy_size" /etc/caddy/Caddyfile "$caddy_candidate"

install -d -o root -g root -m 0700 /srv/snotes/reminder-deploy-step06
printf '%s\n' "$backup_dir" > /srv/snotes/reminder-deploy-step06/backup-dir
cp deploy/greencloud/reminder/reminder.Caddyfile /srv/snotes/reminder-deploy-step06/caddy-block
{
    printf '%s\n' '# snotes-step06-reminder-backup'
    printf 'restrict,command="/usr/local/sbin/snotes-reminder-backup-ssh-entry" %s\n' "$backup_public_key"
} > /srv/snotes/reminder-deploy-step06/authorized-keys-addition
chmod 0600 /srv/snotes/reminder-deploy-step06/*
install -o root -g root -m 0700 deploy/greencloud/reminder/rollback-first.sh /srv/snotes/rollback-step06.sh

install -d -o root -g root -m 0755 /srv/snotes/reminder-runtime
cp -R src /srv/snotes/reminder-runtime/
install -o root -g root -m 0644 source-manifest.json /srv/snotes/reminder-runtime/source-manifest.json
chown -R root:root /srv/snotes/reminder-runtime
find /srv/snotes/reminder-runtime -type d -exec chmod 0755 {} +
find /srv/snotes/reminder-runtime -type f -exec chmod 0644 {} +

if [ -e /srv/snotes/data ]; then
    test -d /srv/snotes/data
    test "$(stat -c '%U:%G:%a' /srv/snotes/data)" = snotes:snotes:700
else
    install -d -o snotes -g snotes -m 0700 /srv/snotes/data
fi
if [ -e /srv/snotes/backups ]; then
    test -d /srv/snotes/backups
    runuser -u snotes -- test -x /srv/snotes/backups
else
    install -d -o root -g root -m 0755 /srv/snotes/backups
fi
install -d -o snotes -g snotes -m 0700 /srv/snotes/backups/reminder
runuser -u snotes -- /usr/bin/env -i -C /srv/snotes/reminder-runtime \
    HOME=/srv/snotes \
    PATH=/srv/snotes/runtime/.venv/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    /srv/snotes/runtime/.venv/bin/python -B -m src.reminder_cloud init-staging
test -f /srv/snotes/data/reminder.db
find /srv/snotes/data -maxdepth 1 -name 'reminder.db*' -exec chown snotes:snotes {} + -exec chmod 0600 {} +

install -o root -g root -m 0755 deploy/greencloud/reminder/snotes-reminder-backup-ssh-entry /usr/local/sbin/snotes-reminder-backup-ssh-entry
install -o root -g root -m 0755 deploy/greencloud/reminder/snotes-reminder-backup-export /usr/local/sbin/snotes-reminder-backup-export
install -o root -g root -m 0440 deploy/greencloud/reminder/snotes-reminder-backup.sudoers /etc/sudoers.d/snotes-reminder-backup
visudo -cf /etc/sudoers.d/snotes-reminder-backup
cat /srv/snotes/reminder-deploy-step06/authorized-keys-addition >> /var/lib/snotes-publish/.ssh/authorized_keys
chown root:snotes-publish /var/lib/snotes-publish/.ssh/authorized_keys
chmod 0640 /var/lib/snotes-publish/.ssh/authorized_keys
cmp -n "$(stat -c %s "$install_backup/authorized_keys.before")" "$install_backup/authorized_keys.before" /var/lib/snotes-publish/.ssh/authorized_keys

for unit in \
    snotes-reminder-api.service \
    snotes-reminder-scheduler.service \
    snotes-reminder-scheduler.timer \
    snotes-reminder-backup.service \
    snotes-reminder-backup.timer
do
    install -o root -g root -m 0644 "deploy/greencloud/reminder/$unit" "/etc/systemd/system/$unit"
done
runuser -u snotes -- /usr/bin/env -i -C /srv/snotes/reminder-runtime \
    HOME=/srv/snotes \
    PATH=/srv/snotes/runtime/.venv/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    /srv/snotes/runtime/.venv/bin/python -B -m src.reminder_cloud backup
test -f /srv/snotes/backups/reminder/reminder-latest.db
chown -R snotes:snotes /srv/snotes/backups/reminder
find /srv/snotes/backups/reminder -type d -exec chmod 0700 {} +
find /srv/snotes/backups/reminder -type f -exec chmod 0600 {} +

systemctl daemon-reload
systemctl enable --now snotes-reminder-api.service
systemctl enable --now snotes-reminder-scheduler.timer snotes-reminder-backup.timer
systemctl is-active --quiet snotes-reminder-api.service
systemctl is-active --quiet snotes-reminder-scheduler.timer
systemctl is-active --quiet snotes-reminder-backup.timer

mv "$caddy_candidate" /etc/caddy/Caddyfile
trap - EXIT HUP INT TERM
cmp -n "$original_caddy_size" "$install_backup/Caddyfile.before" /etc/caddy/Caddyfile
"$caddy_bin" validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl reload caddy

printf 'STEP06_BACKUP_DIR=%s\n' "$backup_dir"
printf '%s\n' 'STEP06_REMINDER_INSTALL=PASS'
