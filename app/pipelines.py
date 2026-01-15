from haystack.components.generators import OpenAIGenerator
from dotenv import load_dotenv
import asyncio

load_dotenv()


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


def generate_notifications_for_patients(patients: list):
    """
    Generate personalized notifications for a list of patients.

    Args:
        patients: List of patient dictionaries with their full information

    Returns:
        List of generated notifications with patient info
    """
    notifications = []

    for patient in patients:
        # Extract relevant patient context (excluding id, firebase_token, notif_in_24h, time_to_notif, created_at)
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

        Keep it concise (1-2 sentences), friendly, motivating, and personalized based on their profile.
        Do not use their name in the notification.
        """

        # Generate notification using OpenAI
        response = openai_client.run(prompt=prompt)
        notification_text = response["replies"][-1]

        notifications.append(
            {
                "patient_id": patient.get("id"),
                "patient_name": patient.get("name"),
                "firebase_token": patient.get("firebase_token"),
                "notification_text": notification_text,
            }
        )

        print(f"Generated notification for {context['name']}: {notification_text}")

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
            firebase_notifications.append({
                "token": notif["firebase_token"],
                "title": "Time to Move!",
                "body": notif["notification_text"],
                "data": {
                    "patient_id": notif["patient_id"],
                    "patient_name": notif["patient_name"],
                }
            })

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
    # Run async function in new event loop
    return asyncio.run(send_notifications_via_firebase_async(notifications))
