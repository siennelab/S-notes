## Web Gale — internal Notes + Reminder stdio

Gale reuses the stable registry ID `gale`. The no-login OS account `snotes-gale`
has a private primary group and no supplementary groups or sudo grant. The
installer selects an actually free UID/GID; runtime resolves that account from
the OS rather than allowing a caller to choose an actor. All business files
remain under `/srv/snotes`.

| Path | Owner:group / mode |
| --- | --- |
| `/srv/snotes/mirror/gale` | `snotes:snotes-gale 2750` |
| `/srv/snotes/mirror/gale/current.db` | `snotes:snotes-gale 0640` |
| `/srv/snotes/actor-credentials/gale` | `root:snotes-gale 0750` |
| `gale/notes-proposal-token`, `gale/reminder-token` | `snotes-gale:snotes-gale 0400` |
| `/srv/snotes/actor-credentials/service` | `root:root 0700` |
| `service/gale-notes-proposal-token` | `root:root 0600` |
| `/srv/snotes/gale-internal` | `root:root 0755`, scripts/records `0644` |
| `/usr/local/bin/snotes-gale-internal-mcp` | `root:root 0755` |

The two new credentials are independent, generated on GreenCloud, and never
printed or transferred to the Mac. Existing owner/actor tokens remain unchanged.
The existing root-only Reminder API mapping gains only the `gale` actor token.
The existing Notes outbox gets one systemd `LoadCredential` drop-in for the
separate Gale Notes token. Neither token authenticates to the other API.
No Note permission, Reminder permission profile, owner command, schema or
authority changes. Other actors retain their existing credentials and entries.

Use the internal entry as the fixed OS user:

```sh
runuser -u snotes-gale -- /usr/local/bin/snotes-gale-internal-mcp
```

Discovery combines the existing `cloud_read_tools(proposal_enabled=True)` with
the existing local MCP registry filtered by Reminder `ACTOR_COMMANDS`:
12 Notes tools and 13 Reminder tools at this baseline. No second tool list or
business implementation is maintained. Notes queues proposals through the
existing localhost `8767` service and `/srv/snotes/data/outbox.db`; Reminder
uses the existing localhost `8766` API and its production authority. Gale
cannot open either database or other actors' mirrors/credentials. Arguments,
environment and payload cannot select another actor. The wrapper rejects
arguments and clears the caller environment. Metadata discovery is published
in the existing mirror schema/catalog; metadata writes still allow only title,
directory and tag edits. Delete, visibility and target writes remain unavailable.

Package from the repository venv, with a temporary HOME:

```sh
.venv/bin/python -B deploy/greencloud/gale-internal/package.py /private/tmp/gale-internal.tar.gz
```

Transfer the archive through the existing `ssh optimal` path into a private
temporary directory, compare SHA256, and extract. As root, run the existing
runtime venv on `deploy/greencloud/gale-internal/install.py`. It verifies every
source hash and sealed permission baseline, backs up only replaced files and
path metadata under `/root/greencloud-backups/gale-internal-*`, updates the
small required source closure, and restarts only Notes outbox and Reminder API.
It does not migrate/open data, create another venv, install dependencies, alter
timers/ntfy, or change any public route. The manifest and deployment record are
saved under `/srv/snotes/gale-internal`.

The Mac retains its one five-minute publisher LaunchAgent and existing private
runtime venv. `install_mac_publisher.py <package-root>` deploys the packaged,
content-pinned publisher source closure into the existing `greencloud-publisher`
runtime house and switches only the existing LaunchAgent's WorkingDirectory.
It preserves the old plist and source for recovery. No new synchronizer, catalog
database, formal Notes copy or Mac authority is added. Production mirror refresh
is performed only by the existing publisher. The Mac source's GreenCloud receipt
and actor refresh contracts now recognize `gale`; this ticket does not build or
install an App bundle or require human acceptance of a production proposal.

For checks, build a fictional fixture on the Mac with
`tests.greencloud_gale_fixture.build_fixtures` and transfer only its manifest
and actor mirrors. Never transfer the temporary source database. Run:

```sh
/srv/snotes/runtime/.venv/bin/python -I -B /srv/snotes/gale-internal/verify_internal.py /tmp/<private-fictional-fixture>
```

The verifier checks real Gale UID/file boundaries and authenticated production
read/reject paths without printing Notes or secrets. Every create/edit/metadata
proposal and Reminder mutation runs against disposable databases in a temporary
service-owned directory, served on ephemeral localhost ports by `snotes`, using
only Gale's own real credentials through private pipes. It runs the deployed
unified MCP composition under the real Gale UID. Servers stop normally; fixtures
are removed. The existing claude/openclaw Notes and Reminder paths are checked
without creating data. No notification transport or scheduler runs in fixtures.

Recovery uses the deployment record's private rollback directory and
`metadata.json`: stop only the two affected API services, restore recorded
preexisting files/ownership/modes, retire newly added entry/drop-in/credentials,
then daemon-reload and restart. Preserve production DBs, mirror state and all
other services. Keep the no-login Gale account until its owned files have been
reviewed; restoring older source and credentials disables access without deleting
user data. For Mac recovery, restore the saved plist and bootstrap the same
LaunchAgent. Oracle, DNS, Caddy, OAuth and connectors are outside this ticket.
