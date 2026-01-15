"""
Firebase Cloud Messaging module for sending push notifications.
"""

import os
import asyncio
from typing import Optional, Dict, Any
import firebase_admin
from firebase_admin import credentials, messaging


# Global variable to track if Firebase has been initialized
_firebase_initialized = False


def initialize_firebase(service_account_path: Optional[str] = None):
    """
    Initialize Firebase Admin SDK.

    Args:
        service_account_path: Path to the Firebase service account JSON file.
                            If not provided, will look for FIREBASE_SERVICE_ACCOUNT_PATH env var.

    Returns:
        True if initialized successfully, False otherwise
    """
    global _firebase_initialized

    if _firebase_initialized:
        return True

    try:
        # Get service account path from parameter or environment variable
        if service_account_path is None:
            service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")

        if not service_account_path:
            print("WARNING: No Firebase service account path provided. Firebase messaging will be disabled.")
            return False

        if not os.path.exists(service_account_path):
            print(f"WARNING: Firebase service account file not found at {service_account_path}")
            return False

        # Initialize Firebase Admin SDK
        cred = credentials.Certificate(service_account_path)
        firebase_admin.initialize_app(cred)
        _firebase_initialized = True
        print("Firebase Admin SDK initialized successfully")
        return True

    except Exception as e:
        print(f"ERROR: Failed to initialize Firebase Admin SDK: {str(e)}")
        return False


async def send_push_notification_async(
    token: str,
    title: str,
    body: str,
    data: Optional[Dict[str, str]] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Send a push notification asynchronously to a single device.

    Args:
        token: Firebase device token
        title: Notification title
        body: Notification body text
        data: Optional dictionary of custom data to include
        dry_run: If True, validate but don't actually send the message

    Returns:
        Dict with status, message, and message_id (if successful)
    """
    # Run the synchronous Firebase call in a thread pool
    return await asyncio.to_thread(
        send_push_notification,
        token=token,
        title=title,
        body=body,
        data=data,
        dry_run=dry_run
    )


def send_push_notification(
    token: str,
    title: str,
    body: str,
    data: Optional[Dict[str, str]] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Send a push notification to a single device.

    Args:
        token: Firebase device token
        title: Notification title
        body: Notification body text
        data: Optional dictionary of custom data to include
        dry_run: If True, validate but don't actually send the message

    Returns:
        Dict with status, message, and message_id (if successful)
    """
    if not _firebase_initialized:
        if not initialize_firebase():
            return {
                "status": "error",
                "message": "Firebase is not initialized. Check service account configuration.",
            }

    try:
        # Build the message
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data or {},
            token=token,
        )

        # Send the message
        response = messaging.send(message, dry_run=dry_run)

        return {
            "status": "success",
            "message": "Notification sent successfully" if not dry_run else "Notification validated successfully (dry run)",
            "message_id": response,
        }

    except messaging.UnregisteredError:
        return {
            "status": "error",
            "message": "Device token is invalid or expired",
        }
    except messaging.SenderIdMismatchError:
        return {
            "status": "error",
            "message": "Device token does not match the sender ID",
        }
    except messaging.QuotaExceededError:
        return {
            "status": "error",
            "message": "Messaging quota exceeded",
        }
    except messaging.InvalidArgumentError as e:
        return {
            "status": "error",
            "message": f"Invalid argument: {str(e)}",
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to send notification: {str(e)}",
        }


async def send_batch_notifications_async(
    notifications: list[Dict[str, Any]],
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Send multiple push notifications asynchronously.

    Args:
        notifications: List of notification dicts, each containing:
            - token: Firebase device token
            - title: Notification title
            - body: Notification body text
            - data: Optional dict of custom data
        dry_run: If True, validate but don't actually send messages

    Returns:
        Dict with status, success_count, failure_count, and results list
    """
    if not _firebase_initialized:
        if not initialize_firebase():
            return {
                "status": "error",
                "message": "Firebase is not initialized",
                "success_count": 0,
                "failure_count": len(notifications),
            }

    # Send all notifications concurrently
    tasks = [
        send_push_notification_async(
            token=notif.get("token"),
            title=notif.get("title", ""),
            body=notif.get("body", ""),
            data=notif.get("data"),
            dry_run=dry_run
        )
        for notif in notifications
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Count successes and failures
    success_count = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "success")
    failure_count = len(results) - success_count

    return {
        "status": "complete",
        "success_count": success_count,
        "failure_count": failure_count,
        "total": len(notifications),
        "results": results,
    }
