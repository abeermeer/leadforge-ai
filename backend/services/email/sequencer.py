"""Follow-up sequence scheduling (PRD 3E).

On first send, schedule step 1 (+3 days) and step 2 (+7 days). Any reply,
unsubscribe, or bounce cancels the remaining scheduled steps.
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from models import SequenceStep, SequenceStepStatus

FOLLOWUP_OFFSETS_DAYS = (3, 7)


def schedule_followups(db: Session, lead, user_id) -> list[SequenceStep]:
    """Create scheduled follow-up steps for a lead after its first send.

    Idempotent: if steps already exist for this lead, does nothing. Bodies are
    generated lazily at send time by run_due_sequences.
    """
    existing = db.query(SequenceStep).filter(SequenceStep.lead_id == lead.id).count()
    if existing:
        return []
    now = datetime.utcnow()
    steps = []
    for i, days in enumerate(FOLLOWUP_OFFSETS_DAYS, start=1):
        step = SequenceStep(
            lead_id=lead.id,
            user_id=user_id,
            step_number=i,
            scheduled_for=now + timedelta(days=days),
            status=SequenceStepStatus.scheduled,
        )
        db.add(step)
        steps.append(step)
    db.commit()
    return steps


def cancel_sequence(db: Session, lead_id) -> int:
    """Cancel all still-scheduled steps for a lead. Returns count cancelled."""
    n = (
        db.query(SequenceStep)
        .filter(
            SequenceStep.lead_id == lead_id,
            SequenceStep.status == SequenceStepStatus.scheduled,
        )
        .update({SequenceStep.status: SequenceStepStatus.cancelled}, synchronize_session=False)
    )
    db.commit()
    return n
