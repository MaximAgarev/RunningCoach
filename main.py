from fastapi import FastAPI, Request, Query, status
from fastapi.responses import JSONResponse
from datetime import date
from pydantic import BaseModel
from typing import Optional
import httpx
import os
import asyncio
import json
from dotenv import load_dotenv

# --- Load environment variables ---
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_VERSION = "2022-06-28"
STATUS_DATABASE_ID = "2229a1646ce180ff8fe3cb424fb6ef6c"

# --- App initialization ---
app = FastAPI()

# --- Headers for Notion API ---
notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json"
}

# --- Models ---
class CreateStatusRequest(BaseModel):
    status: str
    start_date: str
    end_date: Optional[str] = None

class UpdateStatusFlexibleRequest(BaseModel):
    page_id: str
    fields: dict

# --- Reusable Notion logic ---
def create_status(data: CreateStatusRequest):
    properties = {
        "Статус": {
            "title": [{"text": {"content": data.status}}]
        },
        "Дата начала": {
            "date": {"start": data.start_date}
        }
    }
    if isinstance(data.end_date, str) and data.end_date.strip():
        properties["Дата окончания"] = {
            "date": {"start": data.end_date}
        }

    body = {
        "parent": {"database_id": STATUS_DATABASE_ID},
        "properties": properties
    }
    response = httpx.post("https://api.notion.com/v1/pages", headers=notion_headers, json=body)
    return response.json()

def update_status(data: UpdateStatusFlexibleRequest):
    body = {"properties": data.fields}
    response = httpx.patch(
        f"https://api.notion.com/v1/pages/{data.page_id}",
        headers=notion_headers,
        json=body
    )
    return response.json()

def get_statuses(active_only: bool = False, limit: int = 0):
    payload = {
        "sorts": [{"property": "Дата начала", "direction": "descending"}]
    }

    if active_only:
        today = date.today().isoformat()
        payload["filter"] = {
            "or": [
                {"property": "Дата окончания", "date": {"on_or_after": today}},
                {"property": "Дата окончания", "date": {"is_empty": True}}
            ]
        }

    url = f"https://api.notion.com/v1/databases/{STATUS_DATABASE_ID}/query"
    results = []
    has_more = True
    next_cursor = None

    while has_more:
        if next_cursor:
            payload["start_cursor"] = next_cursor

        response = httpx.post(url, headers=notion_headers, json=payload)
        data = response.json()

        for page in data.get("results", []):
            props = page["properties"]
            results.append({
                "id": page["id"],
                "Статус": props["Статус"]["title"][0]["text"]["content"] if props["Статус"]["title"] else None,
                "Дата начала": props["Дата начала"]["date"]["start"] if props["Дата начала"]["date"] else None,
                "Дата окончания": props["Дата окончания"]["date"]["start"] if props["Дата окончания"]["date"] else None
            })
            if limit and len(results) >= limit:
                return results[:limit]

        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    return results

# --- FastAPI route wrappers (optional, kept for manual API use) ---
@app.post("/createStatus")
def route_create_status(data: CreateStatusRequest):
    return create_status(data)

@app.patch("/updateStatus")
def route_update_status(data: UpdateStatusFlexibleRequest):
    return update_status(data)

@app.get("/getStatuses")
def route_get_statuses(active_only: bool = Query(False), limit: int = Query(0)):
    return get_statuses(active_only, limit)

# --- Telegram + OpenAI Assistant endpoint ---
user_threads = {}

@app.post("/ask")
async def ask(request: Request):
    try:
        body = await request.json()
        user_text = body["text"]
        chat_id = str(body["chat_id"])

        async with httpx.AsyncClient() as client:
            if chat_id in user_threads:
                thread_id = user_threads[chat_id]
            else:
                thread_resp = await client.post(
                    "https://api.openai.com/v1/threads",
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "OpenAI-Beta": "assistants=v2",
                        "Content-Type": "application/json"
                    }
                )
                thread_id = thread_resp.json()["id"]
                user_threads[chat_id] = thread_id

            await client.post(
                f"https://api.openai.com/v1/threads/{thread_id}/messages",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v2",
                    "Content-Type": "application/json"
                },
                json={"role": "user", "content": user_text}
            )

            run_resp = await client.post(
                f"https://api.openai.com/v1/threads/{thread_id}/runs",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v2",
                    "Content-Type": "application/json"
                },
                json={"assistant_id": ASSISTANT_ID}
            )
            run_id = run_resp.json()["id"]

            for _ in range(30):
                status_resp = await client.get(
                    f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "assistants=v2"}
                )
                run_status = status_resp.json()
                status_ = run_status["status"]

                if status_ == "completed":
                    break
                elif status_ == "requires_action":
                    tool_calls = run_status["required_action"]["submit_tool_outputs"]["tool_calls"]
                    tool_outputs = []
                    for call in tool_calls:
                        fn_name = call["function"]["name"]
                        args = json.loads(call["function"]["arguments"])

                        if fn_name == "createStatus":
                            result = create_status(CreateStatusRequest(**args))
                        elif fn_name == "updateStatus":
                            result = update_status(UpdateStatusFlexibleRequest(**args))
                        elif fn_name == "getStatuses":
                            result = get_statuses(**args)
                        else:
                            result = {"error": f"Unknown function: {fn_name}"}

                        tool_outputs.append({
                            "tool_call_id": call["id"],
                            "output": json.dumps(result)
                        })

                    await client.post(
                        f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}/submit_tool_outputs",
                        headers={
                            "Authorization": f"Bearer {OPENAI_API_KEY}",
                            "OpenAI-Beta": "assistants=v2",
                            "Content-Type": "application/json"
                        },
                        json={"tool_outputs": tool_outputs}
                    )
                await asyncio.sleep(0.3)

            messages_resp = await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/messages",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "assistants=v2"}
            )
            messages = messages_resp.json()["data"]

            assistant_reply = "🤖 Ошибка: ассистент не сгенерировал ответ."
            for msg in messages:
                if msg["role"] == "assistant" and "content" in msg:
                    for part in msg["content"]:
                        if part["type"] == "text":
                            assistant_reply = part["text"]["value"]
                            break
                    break

            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": assistant_reply}
            )

        return {"status": "ok"}

    except Exception as e:
        print("❌ ERROR:", str(e))
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"error": str(e)})

@app.api_route("/ping", methods=["GET", "HEAD"])
async def ping():
    return {"status": "alive"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=3000)
