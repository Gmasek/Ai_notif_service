from fastapi import FastAPI, Body
from pydantic import BaseModel
from .tasks import ask_question, data_fill_task, update_firebase_token_task, send_firebase_notification_task
from celery.result import AsyncResult
from .celery_app import celery_app
import json

app = FastAPI(title="FastAPI + Celery + Haystack")


@app.get("/ask")
async def ask():
    # Send to Celery asynchronously
    task = ask_question.delay()
    return {"task_id": task.id, "status": "submitted"}


@app.post("/db_fill")
async def db_fill(data_in: dict = Body(...)):
    data = data_in
    if isinstance(data_in, str):
        try:
            data = json.loads(data_in)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON string: {e}")

    # Now `data` is guaranteed to be a dict
    if not isinstance(data, dict):
        raise TypeError("Input must be a dict or JSON string representing a dict")

    task = data_fill_task.delay(data)
    return {"task_id": task.id, "status": "submitted"}


@app.get("/result/{task_id}")
async def get_result(task_id: str):

    result = celery_app.AsyncResult(task_id)
    print(result)
    if result.ready():
        return {"status": "done", "result": result.result}
    return {"status": "pending"}


@app.post("/update_firebase_token")
async def update_firebase_token(data: dict = Body(...)):
    """
    Update a patient's Firebase token.

    Expected JSON body:
    {
        "user_id": "uuid-string",
        "firebase_token": "new-firebase-token"
    }
    """
    user_id = data.get("user_id")
    firebase_token = data.get("firebase_token")

    if not user_id:
        return {"status": "error", "message": "user_id is required"}
    if not firebase_token:
        return {"status": "error", "message": "firebase_token is required"}

    # Send to Celery asynchronously
    task = update_firebase_token_task.delay(user_id, firebase_token)
    return {"task_id": task.id, "status": "submitted"}
