from haystack.components.generators import OpenAIGenerator
from dotenv import load_dotenv
import asyncio
import random
import logging

load_dotenv()

logger = logging.getLogger(__name__)


template = """
You are a notification generator.
Create a short, positive notification text to encourage a person to move more.
Keep it concise, friendly, and safe.
"""

openai_client = OpenAIGenerator(model="gpt-4o-mini")


### Simple llm generation with promp using template
### Can do Async later, but i dont think its necessary
## No context
def generateNotif_gpt():
    response = openai_client.run(prompt=template)
    print(response["replies"][-1])
    return response["replies"][-1]


def build_contextual_information(context_data: dict) -> str:
    """Build contextual information string from context data."""
    missing = [k for k in (
        "day", "time", "user status", "weather", "activity", "location",
        "position of body", "last interaction", "mood_valence", "energetic_arousal",
        "affect_calmness", "stress", "locus_of_control", "close_locations",
        "calendar entries", "motivation_pa", "barrier_pa", "pa_scheduled_today",
        "pa_performed_today", "next_day_text", "pa_scheduled_tomorrow",
    ) if not context_data.get(k)]
    if missing:
        logger.warning("build_contextual_information: missing/empty keys: %s", missing)
    logger.info("build_contextual_information raw keys present: %s", list(context_data.keys()))
    return (
        f"Today - {context_data.get('day')}, between {context_data.get('time')}, I am {context_data.get('user status')}, and the weather is {context_data.get('weather')}. "
        f"I am mainly engaging in {context_data.get('activity')} at {context_data.get('location')}, spending a significant amount of time {context_data.get('position of body')}. My last interaction with my phone is {context_data.get('last interaction')} ago. "
        f"I am feeling {context_data.get('mood_valence')}% well, {context_data.get('energetic_arousal')}% energetic, {context_data.get('affect_calmness')}% calm, {context_data.get('stress')}% stressed, and {context_data.get('locus_of_control')}% in control of what is happening. "
        f"Nearby locations accessible included: {context_data.get('close_locations')}. "
        f"Later that day, I plan the following: {context_data.get('calendar entries')}. "
        f"Independent of my circumstances, I am {context_data.get('motivation_pa')}% motivated to engage in physical activity. My circumstances are {context_data.get('barrier_pa')}% favourable of physical activity. "
        f"I {context_data.get('pa_scheduled_today')} intended a physical activity for today{context_data.get('pa_scheduled_today_yes', '')}{context_data.get('pa_planning_specifity_scheduled_today', '')}. "
        f"I {context_data.get('pa_performed_today')} complete some physical activity today{context_data.get('pa_performed_today_yes', '')}. "
        f"For tomorrow - {context_data.get('next_day_text')} - I {context_data.get('pa_scheduled_tomorrow')} intended to be physically active{context_data.get('pa_scheduled_tomorrow_yes', '')}{context_data.get('pa_planning_specifity_scheduled_tomorrow', '')}."
    )


def build_big5_information(context_data: dict) -> str:
    """Build Big Five personality traits information string."""
    return (
        f"My big five personality traits are the following: I am {context_data.get('extraversion')}; "
        f"{context_data.get('agreeableness')}; {context_data.get('conscientiousness')}; {context_data.get('neuroticism')}; {context_data.get('openness')}. "
    )


def prompt_builder(
    patient: dict,
    personalized: bool,
    context_data: dict = None,
    include_big5: bool = False,
):
    logger.info(
        "prompt_builder inputs — participant=%s, personalized=%s, include_big5=%s",
        patient.get("more_participant_id"),
        personalized,
        include_big5,
    )
    logger.info("prompt_builder patient fields: %s", {
        k: v for k, v in patient.items() if k != "context_data"
    })
    logger.info("prompt_builder context_data: %s", context_data)

    context = {
        "name": patient.get("name"),
        "big5": patient.get("big5"),
        "hobbies": patient.get("hobbies"),
        "tpb": patient.get("tpb"),
        "group_id": patient.get("group_id"),
        "age": patient.get("age"),
        "gender": patient.get("gender"),
        "job_type": patient.get("job_type"),
    }

    # Build contextual information if provided
    contextual_info = ""
    if context_data:
        contextual_info = f"\n        Current context:\n        {build_contextual_information(context_data)}"
        if include_big5:
            contextual_info += f"\n        {build_big5_information(context_data)}"

    if personalized:
        # Create personalized prompt
        prompt = f"""
        You are a notification generator for a health and wellness app.
        Create a short, positive, personalized notification text to encourage this person to move more.

        Person's profile:
        - Name: {context['name']}
        - Age: {context['age']}
        - Gender: {context['gender']}
        - Job: {context['job_type']}
        - Hobbies: {', '.join(context['hobbies']) if context['hobbies'] else 'Not specified'}
        - Big Five Personality Traits: {context['big5'] if context['big5'] else 'Not specified'}
        - Theory of Planned Behavior scores: {context['tpb'] if context['tpb'] else 'Not specified'}
        {contextual_info}

        Keep it concise (1-2 sentences), friendly, motivating, and personalized based on their profile.
        Do not use their name in the notification.
        """
        return prompt
    else:
        prompt = f"""
        You are a notification generator for a health and wellness app.
        Create a short, positive, personalized notification text to encourage this person to move more.

        Person's profile:
        - Name: {context['name']}
        - Age: {context['age']}
        - Gender: {context['gender']}
        - Job: {context['job_type']}
        - Hobbies: {', '.join(context['hobbies']) if context['hobbies'] else 'Not specified'}
        {contextual_info}

        Keep it concise (1-2 sentences), friendly, motivating, and personalized based on their profile.
        Do not use their name in the notification.
        """
        return prompt


def generate_notifications_for_patients(
    patients: list, context_data: dict = None, include_big5: bool = False
):
    """
    Generate personalized notifications for a list of patients.

    Args:
        patients: List of patient dictionaries with their full information
        context_data: Optional dict with contextual information (day, time, weather, activity, etc.)
        include_big5: Whether to include Big Five personality traits in the prompt

    Returns:
        List of generated notifications with patient info
    """
    notifications = []

    for patient in patients:
        # Extract relevant patient context (excluding id, firebase_token, notif_in_24h, time_to_notif, created_at)
        probability = random.randint(1, 10)
        was_personalized = False
        if patient.get("group_id") == 1:
            was_personalized = probability < 4
        elif patient.get("group_id") == 2:
            was_personalized = probability < 7
        else:
            was_personalized = probability < 10
        prompt = prompt_builder(
            patient=patient,
            personalized=was_personalized,
            context_data=patient["context_data"],
            include_big5=include_big5,
        )
        # Generate notification using OpenAI
        logger.info(
            "LLM prompt for participant %s (group=%s, personalized=%s):\n%s",
            patient.get("more_participant_id"),
            patient.get("group_id"),
            was_personalized,
            prompt,
        )
        try:
            response = openai_client.run(prompt=prompt)
            notification_text = response["replies"][-1]
            logger.info(
                "LLM response for participant %s: %s",
                patient.get("more_participant_id"),
                notification_text,
            )
        except Exception as e:
            logger.error("OpenAI API error for participant %s: %s", patient.get("more_participant_id"), str(e))
            notification_text = None

        if notification_text:
            notifications.append(
                {
                    "patient_id": patient.get("id"),
                    "patient_name": patient.get("name"),
                    "firebase_token": patient.get("firebase_token"),
                    "notification_text": notification_text,
                    "was_personalized": was_personalized,
                    "group_id": patient.get("group_id"),
                }
            )
            print(
                f"Generated notification for {patient.get('name')}: {notification_text}"
            )
        else:
            print(
                f"Skipping notification for {patient.get('name')} due to generation failure"
            )

    return notifications


async def send_notifications_via_firebase_async(notifications: list):
    """
    Send generated notifications via Firebase Cloud Messaging asynchronously.

    Args:
        notifications: List of notification dicts from generate_notifications_for_patients

    Returns:
        Dict with sending results
    """
    from .firebase_messaging import send_batch_notifications_async

    # Convert notification format to Firebase format
    firebase_notifications = []
    for notif in notifications:
        if notif.get("firebase_token"):
            firebase_notifications.append(
                {
                    "token": notif["firebase_token"],
                    "title": "Time to Move!",
                    "body": notif["notification_text"],
                    "data": {
                        "patient_id": str(notif["patient_id"]),
                        "patient_name": str(notif["patient_name"]),
                        "was_personalized": str(notif.get("was_personalized", False)),
                        "group_id": str(notif.get("group_id", "")),
                    },
                }
            )

    if not firebase_notifications:
        return {
            "status": "skipped",
            "message": "No valid Firebase tokens found",
            "success_count": 0,
            "failure_count": 0,
        }

    # Send all notifications
    result = await send_batch_notifications_async(firebase_notifications)
    return result


def send_notifications_via_firebase(notifications: list):
    """
    Synchronous wrapper for sending notifications via Firebase.

    Args:
        notifications: List of notification dicts from generate_notifications_for_patients

    Returns:
        Dict with sending results
    """
    # Use existing event loop if available, otherwise create new one
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # If already in an async context, create a new loop in a thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                asyncio.run, send_notifications_via_firebase_async(notifications)
            )
            return future.result()
    else:
        return asyncio.run(send_notifications_via_firebase_async(notifications))
