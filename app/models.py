import uuid
from sqlalchemy import (
    Column,
    Integer,
    String,
    TIMESTAMP,
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class Patient(Base):
    __tablename__ = "patiens"  # matches your SQL table name

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # primary key

    name = Column(String, nullable=False)

    time_to_notif = Column(JSONB)  # mapping of days/times
    firebase_token = Column(String)

    big5 = Column(JSONB)  # Big Five personality
    hobbies = Column(ARRAY(String))  # list of hobbies
    tpb = Column(JSONB)  # Theory of Planned Behavior

    group_id = Column(Integer)  # could also be a ForeignKey if you have groups table

    age = Column(Integer)
    gender = Column(String)
    job_type = Column(String)

    notif_in_24h = Column(Boolean, default=False)  # notification sent in last 24 hours

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    __table_args__ = (CheckConstraint("age >= 0", name="check_age_positive"),)
