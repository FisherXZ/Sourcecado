import asyncio
import pytest
from coworker.people import PersonStore
from test_crm_analysis import tracked_person, ReadOnlyMail, DescriptionModel, worker


@pytest.mark.parametrize("mismatch", ["cc", "bcc", "thread_id"])
def test_changed_full_message_identity_blocks_model_dispatch(tmp_path, mismatch):
    p = PersonStore(tmp_path)
    pid = tracked_person(p)

    class MismatchedMail(ReadOnlyMail):
        def read(self, *, message_id):
            return {
                **super().read(message_id=message_id),
                mismatch: (
                    "other-thread" if mismatch == "thread_id" else "other@example.test"
                ),
            }

    model = DescriptionModel()
    result = asyncio.run(worker(p, model, MismatchedMail()).analyze(pid))
    assert result["status"] == "partial"
    assert result["failure_code"] == "conflicting_context"
    assert model.calls == []


def test_outbound_bcc_is_not_single_person_evidence(tmp_path):
    p = PersonStore(tmp_path)
    pid = tracked_person(p)

    class SharedSentMail(ReadOnlyMail):
        def inbound_message(self, *, message_id):
            meta = super().inbound_message(message_id=message_id)
            return (
                {**meta, "bcc": "another@example.test"}
                if message_id == "sent-1"
                else meta
            )

    model = DescriptionModel()
    result = asyncio.run(worker(p, model, SharedSentMail()).analyze(pid))
    assert (
        result["status"] == "partial"
        and result["failure_code"] == "ambiguous_attribution"
    )
    assert model.calls == []
