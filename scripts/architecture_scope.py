"""Reviewed roles for production modules outside the layered R2 namespace.

This is a boundary inventory, not an exemption list. New flat modules must be
classified; domain/application may never import these infrastructure modules.
The established remote capsules retain their own persistence/runtime rules
instead of being relabelled as pure R2 services without moving their owners.
"""

EXTERNAL_ROLES = {
    # Executable composition/adapter entry points.
    "app": "composition",
    "gale_production_pull_helper": "composition",
    "reminder_helper": "composition",
    "src.actor_mirror_publish": "composition",
    "src.cloud_read_mcp": "composition",
    "src.greencloud_internal_mcp": "composition",
    "src.greencloud_remote_mcp": "composition",
    "src.claude_web_remote_mcp": "composition",
    "src.remote_reader": "composition",
    "src.gale_publish": "composition",
    "src.gale_remote_mcp": "remote-adapter",
    "src.gale_submission_staging_mcp": "remote-adapter",
    # Cloud runtime owns DB readiness/init/backup, not Notes application rules.
    "src.cloud_outbox": "cloud-runtime",
    "src.reminder_cloud": "cloud-runtime",
    # Mac publisher composes R2 services, storage and SSH/file transport.
    "src.services.actor_mirror_publish_service": "composition",
    # Existing Oracle/Cloud capsules: SQL is confined to these named owners.
    "src.services.gale_submission_outbox": "remote-persistence",
    "src.services.gale_remote_service": "remote-persistence",
    "src.services.gale_mirror_service": "remote-persistence",
    # Local publish retains source snapshot SQL; server transactions have their own owner.
    "src.services.gale_publish_service": "remote-operator",
    "src.services.gale_publish_contract": "pure-support",
    "src.services.gale_remote_activation": "remote-operator",
    "src.services.gale_oauth": "remote-adapter",
    # Python scripts installed as runtime commands, not installers or probes.
    "deploy.greencloud.outbox.transport": "remote-adapter",
    "deploy.greencloud.outbox.backup": "remote-operator",
    "deploy.greencloud.ai-access.reminder_actor": "remote-adapter",
    "src.app_paths": "runtime-support",
    "src.search_query": "pure-support",
    "src.ai_targets": "pure-support",
    "src.tag_manager": "pure-support",
    "src.time_utils": "pure-support",
    # Frozen R1 definitions, still used by historical fixture/export support.
    # Oracle validation/search imports do not execute the old Notes write chain.
    "src.database": "frozen-reference",
    "src.redaction": "frozen-reference",
    "src.services.ai_access_service": "frozen-reference",
    "src.services.ai_safe_materialization": "frozen-reference",
    "src": "package",
    "src.services": "package",
}

DEPLOYED_RUNTIME_FILES = (
    "deploy/greencloud/outbox/transport.py",
    "deploy/greencloud/outbox/backup.py",
    "deploy/greencloud/ai-access/reminder_actor.py",
)

SQL_ROLES = frozenset({"cloud-runtime", "remote-persistence", "remote-operator"})

# These are existing compatibility calls within the Oracle export/validation
# capsule, not new permission or storage entry points for the R2 application.
COMPATIBILITY_EDGES = {
    ("src.services.gale_mirror_service", "src.services.ai_access_service",
     "materialize_ai_safe_notes_for_target"),
}
