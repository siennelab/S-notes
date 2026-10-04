"""Retry durable GreenCloud decisions through their original transport."""

from __future__ import annotations

from dataclasses import dataclass

from src.services.actor_mirror_publish_service import ActorMirrorPublisher
from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.application.ports import GreenCloudOutboxTransport


@dataclass(frozen=True, slots=True)
class GreenCloudFollowupResult:
    processed_count: int
    sent_count: int
    mirror_published_count: int
    failed_count: int


class GreenCloudReceiptService:
    def __init__(
        self,
        receipts: AiReceiptInboxService,
        transport: GreenCloudOutboxTransport,
        publisher: ActorMirrorPublisher,
    ) -> None:
        self._receipts = receipts
        self._transport = transport
        self._publisher = publisher

    def flush(self) -> GreenCloudFollowupResult:
        decisions = self._receipts.pending_greencloud_terminal_receipts()
        sent = published = failed = 0
        for decision in decisions:
            try:
                if decision.terminal_state == "accepted" and not decision.mirror_published:
                    result = self._publisher.publish_actor(decision.source_actor)
                    if result.status not in {"published", "unchanged"}:
                        raise RuntimeError("actor_mirror_publish_failed")
                    self._receipts.mark_greencloud_mirror_published(
                        decision.receipt_id, decision.fingerprint
                    )
                    published += 1
                # Once Cloud reports accepted, its next read must already see
                # the published current.db. A failed publish remains retryable.
                self._transport.write_terminal_receipt(
                    decision.source_actor,
                    decision.cloud_request_id,
                    decision.terminal_state,
                    decision.receipt_payload,
                )
                self._receipts.mark_greencloud_receipt_sent(
                    decision.receipt_id, decision.fingerprint
                )
                sent += 1
            except Exception:
                # One unavailable actor/transport must not drop other decisions.
                # The processed inbox row is the durable intent; nothing is
                # marked sent until the remote idempotent receipt succeeds.
                failed += 1
        return GreenCloudFollowupResult(len(decisions), sent, published, failed)
