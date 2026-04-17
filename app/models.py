import uuid
from sqlalchemy import Column, Integer, String, TIMESTAMP, Boolean, Text
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


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    more_participant_id = Column(Integer, nullable=True)
    notification_text = Column(String, nullable=False)
    big5_used = Column(Boolean, default=False)
    group_id = Column(Integer, nullable=True)
    sent_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
