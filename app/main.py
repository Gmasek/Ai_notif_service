from fastapi import FastAPI, Body
from pydantic import BaseModel
from .tasks import ask_question, data_fill_task
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
