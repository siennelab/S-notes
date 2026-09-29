# S-notes GreenCloud Notes Read — Step 5

Target: SSH alias `optimal`, hostname `optimal-living`, Debian 13.7,
system Python 3.13.5. Oracle/Gale is a different machine.

## Installed identities and permissions

- `snotes`: UID/GID 2006, home `/srv/snotes`, shell `/usr/sbin/nologin`.
- `snotes-publish`: UID/GID 987, home `/var/lib/snotes-publish`, shell `/bin/sh`,
  password locked. Only the dedicated Mac key is authorized, with
  `restrict,command="/usr/local/sbin/snotes-mirror-ssh-entry"`.
- Existing `claude` UID/GID 2001 and `openclaw` UID/GID 2002 are unchanged.
- UIDs 2007–2011 were not used.

| Path | Owner:group | Mode |
| --- | --- | --- |
| `/srv/snotes` | root:root | 0755 |
| `/srv/snotes/runtime` | root:root | 0755 |
| `/srv/snotes/mirror` | root:root | 0711 |
| `/srv/snotes/mirror/claude-code` | snotes:claude | 2750 |
| `/srv/snotes/mirror/clawbot` | snotes:openclaw | 2750 |
| each `current.db` | snotes:corresponding actor group | 0640 |
| incoming candidates and publish locks | snotes:corresponding actor group | 0600 |
| `/var/lib/snotes-publish/.ssh/authorized_keys` | root:snotes-publish | 0640 |
| `/etc/sudoers.d/snotes-mirror-receiver` | root:root | 0440 |

Actor groups have no write permission on either their directory or database.
The service owns mirror directories and can atomically replace files. The
transport account cannot modify its authorized key, wrappers, runtime, or sudoers.

## Runtime and receiver

`/usr/local/bin/snotes-cloud-read` launches stdio MCP on demand. There is no
S-notes daemon, listener, HTTP route, or systemd unit. Invoke it as:

```sh
runuser -u claude -- /usr/local/bin/snotes-cloud-read
runuser -u openclaw -- /usr/local/bin/snotes-cloud-read
```

Effective UID fixes the actor and mirror path. Each read opens `current.db`
with `mode=ro&immutable=1` and closes it. Only `search_notes`, `get_note`, and
`get_note_markdown` are registered. Runtime client configuration/discovery is
deferred until Claude Code/OpenClaw installation.

SSH entry rejects requested commands before invoking the exact, no-argument
receiver through sudo as `snotes`. The receiver accepts a bounded JSON header
`{actor,size,sha256}` followed by exactly that many bytes and EOF. Allowed actors
are `claude-code` and `clawbot`; maximum candidate size is 512 MiB, timeout 120 s.
It checks SHA-256 and the existing Step 4 actor validator at mode 0600, then
changes mode to 0640 and atomically replaces `current.db` under an actor lock.
Invalid candidates leave the existing database unchanged. No Notes source DB
is sent to this host.

## Deployment and maintenance

From the Step 5 checkout, use the existing Mac repository Python to run
`scripts/package_greencloud_read.py <archive>`. The archive contains only the
Cloud read/receiver Python dependency closure and deployment assets. Transfer
the archive and the Mac transport **public** key to a private remote temporary
directory, naming the public key `mirror-transport.pub`; unpack and run
`sh deploy/greencloud/install-first.sh` as root.

The installer is intentionally first-install-only and refuses existing assets.
Do not rerun it against this installed system. Runtime source is root-owned at
`/srv/snotes/runtime`, with an application venv using the existing system Python
and `mcp==1.28.1`. `source-manifest.json` records deployed source hashes and the
implementation commit; `requirements-installed.txt` records actual dependencies.
The Mac private key never leaves the Mac.

## Rollback

Stop the Mac schedule first:

```sh
launchctl bootout gui/$(id -u)/com.snotes.greencloud-mirror-publish
```

Move its plist out of `~/Library/LaunchAgents` into a private recovery directory.
Then, through the existing administrative connection:

```sh
ssh optimal /srv/snotes/rollback-step05.sh
```

The script retires this step's authorized key, sudoers, and launch wrappers into
a mode-0700 timestamped directory, preserving their original directory paths,
and terminates only the two actor users' Step 5 MCP processes. It preserves the
runtime, identities, and last successful mirrors for recovery. Re-enabling uses
the archived files at their original paths with their preserved modes/owners.

This was the first S-notes installation on GreenCloud: no prior S-notes files
were overwritten. Caddy, ntfy, de-node, egress, Gale, Reminder, and the Mac Notes
database are outside this rollback. No machine-wide backup is involved.
