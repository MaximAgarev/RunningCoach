from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi import status
import httpx
import os
import asyncio

app = FastAPI()

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ASSISTANT_ID = os.environ["ASSISTANT_ID"]

@app.post("/ask")
async def ask(request: Request):
    try:
        body = await request.json()
        user_text = body["text"]
        chat_id = body["chat_id"]

        async with httpx.AsyncClient() as client:
            # 1. Create thread
            thread_resp = await client.post(
                "https://api.openai.com/v1/threads",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v2",
                    "Content-Type": "application/json"
                }
            )
            thread_id = thread_resp.json()["id"]

            # 2. Post message
            await client.post(
                f"https://api.openai.com/v1/threads/{thread_id}/messages",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v2",
                    "Content-Type": "application/json"
                },
                json={
                    "role": "user",
                    "content": user_text
                }
            )

            # 3. Start run
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

            # 4. Poll run status (с меньшей задержкой)
            for _ in range(30):  # до 9 секунд ожидания
                run_status_resp = await client.get(
                    f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "OpenAI-Beta": "assistants=v2"
                    }
                )
                run_status = run_status_resp.json()
                if run_status["status"] == "completed":
                    break
                await asyncio.sleep(0.3)

            # 5. Get messages
            messages_resp = await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/messages",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v2"
                }
            )
            messages = messages_resp.json()["data"]

            # DEBUG: покажем все роли и куски content
            print("📨 THREAD MESSAGES:")
            for msg in messages:
                print(f" - role: {msg['role']}")
                print(f"   content: {msg.get('content')}")

            # 6. Найдём первый текст от ассистента
            assistant_reply = "🤖 Ошибка: ассистент не сгенерировал текстовый ответ."
            for msg in messages:
                if msg["role"] == "assistant" and "content" in msg:
                    for part in msg["content"]:
                        if part["type"] == "text":
                            assistant_reply = part["text"]["value"]
                            break
                if assistant_reply != "🤖 Ошибка: ассистент не сгенерировал текстовый ответ.":
                    break

            # 7. Отправим в Telegram
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": assistant_reply
                }
            )

        return {"status": "ok"}

    except Exception as e:
        print("❌ ERROR:", str(e))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": str(e)}
        )

@app.api_route("/ping", methods=["GET", "HEAD"])
async def ping():
    return {"status": "alive"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=3000)
