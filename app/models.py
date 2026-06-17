import uuid
from sqlalchemy import (
    Column, Integer, String, TIMESTAMP, Boolean, Text, Date, Numeric, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class Patient(Base):
    """
    Notification state and full participant profile.

    Profile data (Big Five, TPB, demographics, hobbies, notification schedule)
    is populated by periodic_task from Elasticsearch and stored here as the
    single source of truth for the notification pipeline.
    """
    __tablename__ = "patients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    more_participant_id = Column(Integer, unique=True, nullable=True)
    name = Column(Text, nullable=False, default="unknown")
    group_id = Column(Integer, nullable=True)  # A/B-test group: 0, 1, or 2

    # Profile — populated from Elasticsearch baseline survey by periodic_task
    big5 = Column(JSONB, nullable=True)
    hobbies = Column(ARRAY(Text), nullable=True)
    tpb = Column(JSONB, nullable=True)
    time_to_notif = Column(JSONB, nullable=True)  # {day: {start, end}} — drives notification windows
    age = Column(Integer, nullable=True)
    gender = Column(Text, nullable=True)
    job_type = Column(Text, nullable=True)

    # LimeSurvey participant identifiers (optional, for direct LimeSurvey lookups)
    lime_response_id = Column(Integer, nullable=True)
    lime_token = Column(Text, nullable=True)

    notif_in_24h = Column(Boolean, default=False)
    daily_survey_triggered_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class EveningFollowupResponse(Base):
    __tablename__ = "evening_followup_responses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    more_participant_id = Column(Integer, nullable=True)
    submitdate = Column(TIMESTAMP(timezone=True), nullable=True)
    exercised = Column(Boolean, nullable=True)
    activity = Column(Text, nullable=True)
    duration = Column(Text, nullable=True)
    when_exercised = Column(Text, nullable=True)
    other_activity = Column(Boolean, nullable=True)
    other_activity_desc = Column(Text, nullable=True)
    other_duration = Column(Text, nullable=True)
    fetched_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    more_participant_id = Column(Integer, nullable=True)
    notification_text = Column(String, nullable=False)
    big5_used = Column(Boolean, default=False)
    group_id = Column(Integer, nullable=True)
    pipeline = Column(Integer, nullable=True)  # 0=basic_context, 1=rag, 2=agentic
    sent_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    feedback_raw = Column(JSONB, nullable=True)


class DailyCheckin(Base):
    """Today's check-in data cached from LimeSurvey. One row per participant, cleared at daily reset."""
    __tablename__ = "daily_checkins"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    more_participant_id = Column(Integer, unique=True, nullable=False)
    checkin_data = Column(JSONB, nullable=False)
    fetched_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class GeneratedNotification(Base):
    """LLM-generated notification for today. One row per participant, cleared at daily reset.
    Persisted before send so retries reuse the text instead of re-calling the LLM."""
    __tablename__ = "generated_notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    more_participant_id = Column(Integer, unique=True, nullable=False)
    notification_text = Column(Text, nullable=False)
    group_id = Column(Integer, nullable=True)
    pipeline = Column(Integer, nullable=True)  # 0=basic_context, 1=rag, 2=agentic
    send_status = Column(Text, default="pending")  # pending / sent / failed
    generated_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    sent_at = Column(TIMESTAMP(timezone=True), nullable=True)


class NotificationExample(Base):
    """Denormalized example store — one row per (participant, day) carrying the full
    numerical story of a sent notification: how the user felt (check-in numerics) →
    what was generated → how they graded it → whether they exercised.

    Context is snapshotted at send time (before the daily reset wipes DailyCheckin);
    feedback grade + execution outcome are filled in by the nightly enrich task.
    `executed` comes from the evening follow-up survey (LIME_EVENING_FOLLOWUP_SURVEY_ID,
    via EveningFollowupResponse.exercised). Accumulates across days — NOT cleared at daily
    reset — and is the single table the rag/agentic pipelines read at generation time."""
    __tablename__ = "notification_examples"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    more_participant_id = Column(Integer, nullable=False)
    notif_date = Column(Date, nullable=False)

    big5 = Column(JSONB, nullable=True)
    pipeline = Column(Integer, nullable=True)  # 0=basic_context, 1=rag, 2=agentic
    notification_text = Column(Text, nullable=False)

    # Feeling snapshot (from the day's check-in)
    mood_valence = Column(Integer, nullable=True)
    energetic_arousal = Column(Integer, nullable=True)
    locus_of_control = Column(Integer, nullable=True)
    stress = Column(Integer, nullable=True)
    motivation_pa = Column(Integer, nullable=True)
    barrier_pa = Column(Integer, nullable=True)
    plans_pa_today = Column(Text, nullable=True)
    pa_scheduled_today = Column(Text, nullable=True)
    pa_change_reason = Column(Text, nullable=True)
    events_today = Column(Text, nullable=True)

    # Enrichment (nightly)
    feedback_raw = Column(JSONB, nullable=True)
    feedback_grade = Column(Integer, nullable=True)  # 1=good, 2=neutral, 3=bad, NULL=no feedback
    feedback_score = Column(Numeric, nullable=True)
    executed = Column(Boolean, nullable=True)  # from evening follow-up survey (did_you_exercise)
    execution_activity = Column(Text, nullable=True)
    enriched_at = Column(TIMESTAMP(timezone=True), nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("more_participant_id", "notif_date", name="uq_example_participant_date"),
    )
