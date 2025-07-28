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
PLAN_DATABASE_ID = "23d9a1646ce18016b67fe46842dec598"
RUN_DATABASE_ID = "2229a1646ce1811ab7cfe8441534fbad"

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

class UpdatePageRequest(BaseModel):
    page_id: str
    fields: dict

class CreatePageRequest(BaseModel):
    database_id: str
    properties: dict

# --- Reusable Notion logic ---
def create_page(database_id: str, properties: dict):
    body = {"parent": {"database_id": database_id}, "properties": properties}
    response = httpx.post("https://api.notion.com/v1/pages", headers=notion_headers, json=body)
    return response.json()

def update_page(page_id: str, fields: dict):
    body = {"properties": fields}
    response = httpx.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=notion_headers,
        json=body
    )
    return response.json()

# --- Status logic ---
def create_status(data: CreateStatusRequest):
    properties = {
        "Статус": {"title": [{"text": {"content": data.status}}]},
        "Дата начала": {"date": {"start": data.start_date}}
    }
    if isinstance(data.end_date, str) and data.end_date.strip():
        properties["Дата окончания"] = {"date": {"start": data.end_date}}
    return create_page(STATUS_DATABASE_ID, properties)

def update_status(data: UpdateStatusFlexibleRequest):
    return update_page(data.page_id, data.fields)

def get_statuses(active_only: bool = False, limit: int = 0):
    payload = {"sorts": [{"property": "Дата начала", "direction": "descending"}]}
    if active_only:
        today = date.today().isoformat()
        payload["filter"] = {
            "or": [
                {"property": "Дата окончания", "date": {"on_or_after": today}},
                {"property": "Дата окончания", "date": {"is_empty": True}}
            ]
        }
    url = f"https://api.notion.com/v1/databases/{STATUS_DATABASE_ID}/query"
    results, has_more, next_cursor = [], True, None
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

def get_plans(limit: int = 0):
    payload = {"sorts": [{"property": "Дата", "direction": "ascending"}]}
    url = f"https://api.notion.com/v1/databases/{PLAN_DATABASE_ID}/query"
    results, has_more, next_cursor = [], True, None
    while has_more:
        if next_cursor:
            payload["start_cursor"] = next_cursor
        response = httpx.post(url, headers=notion_headers, json=payload)
        data = response.json()
        for page in data.get("results", []):
            props = page["properties"]
            results.append({
                "id": page["id"],
                "Дата": props["Дата"]["date"]["start"] if props["Дата"]["date"] else None,
                "Тип": props["Тип"]["select"]["name"] if props["Тип"]["select"] else None,
                "Задание": props["Задание"]["rich_text"][0]["text"]["content"] if props["Задание"]["rich_text"] else None,
                "Комментарий": props["Комментарий"]["rich_text"][0]["text"]["content"] if props["Комментарий"]["rich_text"] else None,
                "Факт": [rel["id"] for rel in props["Факт"]["relation"]] if props["Факт"]["relation"] else []
            })
            if limit and len(results) >= limit:
                return results[:limit]
        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")
    return results

def get_runs(limit: int = 0):
    payload = {"sorts": [{"property": "Дата", "direction": "descending"}]}
    url = f"https://api.notion.com/v1/databases/{RUN_DATABASE_ID}/query"
    results, has_more, next_cursor = [], True, None
    while has_more:
        if next_cursor:
            payload["start_cursor"] = next_cursor
        response = httpx.post(url, headers=notion_headers, json=payload)
        data = response.json()
        for page in data.get("results", []):
            props = page["properties"]
            results.append({
                "id": page["id"],
                "Дата": props["Дата"]["date"]["start"] if props["Дата"]["date"] else None,
                "Время (мин)": props["Время (мин)"]["number"],
                "Дистанция (км)": props["Дистанция (км)"]["number"],
                "Самочувствие": props["Самочувствие"]["rich_text"][0]["text"]["content"] if props["Самочувствие"]["rich_text"] else None,
                "Комментарий": props["Комментарий"]["rich_text"][0]["text"]["content"] if props["Комментарий"]["rich_text"] else None,
                "План": [rel["id"] for rel in props["План"]["relation"]] if props["План"]["relation"] else []
            })
            if limit and len(results) >= limit:
                return results[:limit]
        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")
    return results

# --- FastAPI route wrappers ---
@app.post("/createStatus")
def route_create_status(data: CreateStatusRequest):
    return create_status(data)

@app.patch("/updateStatus")
def route_update_status(data: UpdateStatusFlexibleRequest):
    return update_status(data)

@app.get("/getStatuses")
def route_get_statuses(active_only: bool = Query(False), limit: int = Query(0)):
    return get_statuses(active_only, limit)

@app.get("/getPlans")
def route_get_plans(limit: int = Query(0)):
    return get_plans(limit)

@app.get("/getRuns")
def route_get_runs(limit: int = Query(0)):
    return get_runs(limit)

@app.post("/createPlan")
def route_create_plan(data: CreatePageRequest):
    return create_page(PLAN_DATABASE_ID, data.properties)

@app.patch("/updatePlan")
def route_update_plan(data: UpdatePageRequest):
    return update_page(data.page_id, data.fields)

@app.post("/createRun")
def route_create_run(data: CreatePageRequest):
    return create_page(RUN_DATABASE_ID, data.properties)

@app.patch("/updateRun")
def route_update_run(data: UpdatePageRequest):
    return update_page(data.page_id, data.fields)

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
                        elif fn_name == "createPlan":
                            result = create_page(PLAN_DATABASE_ID, args["properties"])
                        elif fn_name == "updatePlan":
                            result = update_page(args["page_id"], args["fields"])
                        elif fn_name == "createRun":
                            result = create_page(RUN_DATABASE_ID, args["properties"])
                        elif fn_name == "updateRun":
                            result = update_page(args["page_id"], args["fields"])
                        elif fn_name == "getPlans":
                            result = get_plans()
                        elif fn_name == "getRuns":
                            result = get_runs()
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
