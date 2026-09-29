#!/bin/sh
# First installation only. Run as root from the unpacked Step 5 package.
set -eu
umask 077
test "$(id -u)" = 0
test "$(id -u claude)" = 2001
test "$(id -u openclaw)" = 2002
test ! -e /srv/snotes
test ! -e /var/lib/snotes-publish
! getent passwd snotes >/dev/null
! getent passwd snotes-publish >/dev/null
! getent passwd 2006 >/dev/null
! getent group 2006 >/dev/null
for target in /usr/local/bin/snotes-cloud-read /usr/local/sbin/snotes-mirror-ssh-entry /usr/local/sbin/snotes-mirror-receiver /etc/sudoers.d/snotes-mirror-receiver; do
    test ! -e "$target"
done
test -f mirror-transport.pub
ssh-keygen -l -f mirror-transport.pub >/dev/null
groupadd --gid 2006 snotes
useradd --uid 2006 --gid snotes --home-dir /srv/snotes --shell /usr/sbin/nologin snotes
useradd --system --user-group --home-dir /var/lib/snotes-publish --shell /bin/sh snotes-publish
install -d -o root -g root -m 0755 /srv/snotes /srv/snotes/runtime
install -d -o root -g root -m 0711 /srv/snotes/mirror
install -d -o snotes -g claude -m 2750 /srv/snotes/mirror/claude-code
install -d -o snotes -g openclaw -m 2750 /srv/snotes/mirror/clawbot
cp -R src /srv/snotes/runtime/
cp source-manifest.json /srv/snotes/runtime/
chown -R root:root /srv/snotes/runtime
find /srv/snotes/runtime -type d -exec chmod 0755 {} +
find /srv/snotes/runtime -type f -exec chmod 0644 {} +
python3 -m venv /srv/snotes/runtime/.venv
/srv/snotes/runtime/.venv/bin/python -m pip --disable-pip-version-check install 'mcp==1.28.1'
/srv/snotes/runtime/.venv/bin/python -m pip freeze > /srv/snotes/runtime/requirements-installed.txt
chmod -R go+rX /srv/snotes/runtime
install -o root -g root -m 0644 deploy/greencloud/INSTALL.md /srv/snotes/INSTALL.md
install -o root -g root -m 0755 deploy/greencloud/snotes-cloud-read /usr/local/bin/snotes-cloud-read
install -o root -g root -m 0755 deploy/greencloud/snotes-mirror-ssh-entry /usr/local/sbin/snotes-mirror-ssh-entry
install -o root -g root -m 0755 deploy/greencloud/snotes-mirror-receiver /usr/local/sbin/snotes-mirror-receiver
install -o root -g root -m 0700 deploy/greencloud/rollback-first.sh /srv/snotes/rollback-step05.sh
install -d -o root -g snotes-publish -m 0750 /var/lib/snotes-publish /var/lib/snotes-publish/.ssh
{ printf 'restrict,command="/usr/local/sbin/snotes-mirror-ssh-entry" '; cat mirror-transport.pub; } > /var/lib/snotes-publish/.ssh/authorized_keys
chown root:snotes-publish /var/lib/snotes-publish/.ssh/authorized_keys
chmod 0640 /var/lib/snotes-publish/.ssh/authorized_keys
printf '%s\n' 'snotes-publish ALL=(snotes) NOPASSWD: /usr/local/sbin/snotes-mirror-receiver ""' > /etc/sudoers.d/snotes-mirror-receiver
chmod 0440 /etc/sudoers.d/snotes-mirror-receiver
visudo -cf /etc/sudoers.d/snotes-mirror-receiver
getent passwd snotes snotes-publish
printf '%s\n' 'SNOTES_STEP05_INSTALL=PASS'
