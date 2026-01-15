# AI Notification Prototype

This project is a health and wellness notification system that generates personalized notifications for users based on their profiles and schedules.

## Features

- Scheduled notifications based on user time windows
- Personalized notification generation using OpenAI
- Big Five personality traits and Theory of Planned Behavior integration
- Celery-based task scheduling
- PostgreSQL database with JSONB support

## Setup

1. Start the services:
```bash
docker-compose up --build
```

2. The API will be available at `http://localhost:8000`

## API Endpoints

### Create Patient

**Endpoint:** `POST http://localhost:8000/db_fill`

**Description:** Creates a new patient record in the database with their profile information and notification schedule.

**Request Body:**

```json
{
  "name": "John Doe",
  "time_to_notif": {
    "monday": { "start": "15:00", "end": "17:00" },
    "tuesday": { "start": "10:00", "end": "18:00" },
    "wednesday": { "start": "09:30", "end": "17:30" },
    "thursday": { "start": "08:00", "end": "16:00" },
    "friday": { "start": "09:00", "end": "15:00" },
    "saturday": { "start": "10:00", "end": "14:00" },
    "sunday": { "start": "11:00", "end": "13:00" }
  },
  "firebase_token": "fcm_token_example_1234567890",
  "big5": {
    "openness": 0.8,
    "conscientiousness": 0.6,
    "extraversion": 0.7,
    "agreeableness": 0.9,
    "neuroticism": 0.3
  },
  "hobbies": ["reading", "cycling", "painting"],
  "tpb": {
    "attitude": 0.75,
    "subjective_norm": 0.6,
    "perceived_behavioral_control": 0.8,
    "intention": 0.7
  },
  "group_id": 2,
  "age": 28,
  "gender": "male",
  "job_type": "software engineer"
}
```

**Field Descriptions:**

- `name` (required): Patient's full name
- `time_to_notif` (optional): Notification time windows for each day of the week (use lowercase day names)
  - Each day has a `start` and `end` time in HH:MM format (24-hour)
- `firebase_token` (optional): Firebase Cloud Messaging token for push notifications
- `big5` (optional): Big Five personality trait scores (values between 0 and 1)
  - `openness`: Openness to experience
  - `conscientiousness`: Conscientiousness
  - `extraversion`: Extraversion
  - `agreeableness`: Agreeableness
  - `neuroticism`: Neuroticism
- `hobbies` (optional): Array of hobby strings
- `tpb` (optional): Theory of Planned Behavior scores (values between 0 and 1)
  - `attitude`: Attitude toward the behavior
  - `subjective_norm`: Perceived social pressure
  - `perceived_behavioral_control`: Perceived ease/difficulty
  - `intention`: Intention to perform the behavior
- `group_id` (optional): Group identifier for experiments
- `age` (optional): Patient's age (must be >= 0)
- `gender` (optional): Gender identifier
- `job_type` (optional): Job or occupation type

**Example using cURL:**

```bash
curl -X POST http://localhost:8000/db_fill \
  -H "Content-Type: application/json" \
  -d '{
    "name": "John Doe",
    "time_to_notif": {
      "monday": { "start": "15:00", "end": "17:00" },
      "tuesday": { "start": "10:00", "end": "18:00" }
    },
    "age": 28,
    "gender": "male",
    "job_type": "software engineer"
  }'
```

## How It Works

### Notification System

1. **Celery Beat Scheduler**: Runs a periodic task every 100 seconds (configurable)
2. **Patient Matching**: Finds patients whose notification time window includes the current time
3. **Notification Generation**: Uses OpenAI to create personalized notifications based on patient profiles
4. **Notification Tracking**: Marks patients as notified to prevent duplicate notifications within 24 hours
5. **Midnight Reset**: Resets all notification flags daily at midnight

### Time Window Matching

The system checks if the current time falls within a patient's notification window:
- Day names must be lowercase (monday, tuesday, etc.)
- Time format: HH:MM (24-hour format)
- A patient matches if: `start_time <= current_time <= end_time`

## Database Reset

If you need to reset the database (e.g., after schema changes):

```bash
docker-compose down -v
docker-compose up --build
```

## Services

- **API**: FastAPI application (port 8000)
- **Worker**: Celery worker for task execution
- **Beat**: Celery beat scheduler for periodic tasks
- **PostgreSQL**: Database (port 5432)
- **Redis**: Message broker for Celery (port 6379)

## Logs

View logs for specific services:

```bash
docker-compose logs -f worker
docker-compose logs -f beat
docker-compose logs -f api
```
