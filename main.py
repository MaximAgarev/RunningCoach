from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi import status
import httpx
import os
import asyncio
import json

app = FastAPI()

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ASSISTANT_ID = os.environ["ASSISTANT_ID"]

# Временное хранилище: chat_id -> thread_id
user_threads = {}

@app.post("/ask")
async def ask(request: Request):
    try:
        body = await request.json()
        user_text = body["text"]
        chat_id = str(body["chat_id"])

        async with httpx.AsyncClient() as client:
            # 1. Получить или создать thread
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
                thread_data = thread_resp.json()
                thread_id = thread_data.get("id")
                if not thread_id:
                    raise ValueError(f"❌ Не удалось создать thread: {thread_data}")
                user_threads[chat_id] = thread_id

            print(f"📩 Пользователь: {user_text}")
            print(f"📎 Thread: {thread_id}")

            # 2. Отправить сообщение
            msg_resp = await client.post(
                f"https://api.openai.com/v1/threads/{thread_id}/messages",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v2",
                    "Content-Type": "application/json"
                },
                json={"role": "user", "content": user_text}
            )
            if msg_resp.status_code >= 400:
                raise ValueError(f"❌ Ошибка отправки сообщения: {msg_resp.text}")

            # 3. Запустить run
            run_resp = await client.post(
                f"https://api.openai.com/v1/threads/{thread_id}/runs",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v2",
                    "Content-Type": "application/json"
                },
                json={"assistant_id": ASSISTANT_ID}
            )
            run = run_resp.json()
            print("🚀 Ответ от /runs:", run)

            run_id = run.get("id")
            if not run_id:
                raise ValueError(f"❌ Ошибка запуска run: нет id в ответе: {run}")

            # 4. Ожидание завершения run или requires_action
            for _ in range(30):
                run_status_resp = await client.get(
                    f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "OpenAI-Beta": "assistants=v2"
                    }
                )
                run_status = run_status_resp.json()
                status_ = run_status["status"]
                print(f"⏳ Статус run: {status_}")

                if status_ in ["completed", "failed"]:
                    break

                elif status_ == "requires_action":
                    tool_calls = run_status["required_action"]["submit_tool_outputs"]["tool_calls"]
                    tool_outputs = []
                    for call in tool_calls:
                        function_name = call["function"]["name"]
                        arguments = json.loads(call["function"]["arguments"])
                        print(f"🛠 Вызов функции {function_name} с аргументами {arguments}")

                        notion_response = await client.post(
                            f"http://notion-proxy:8000/{function_name}",
                            json=arguments
                        )
                        notion_result = notion_response.json()

                        tool_outputs.append({
                            "tool_call_id": call["id"],
                            "output": json.dumps(notion_result)
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

            # 5. Получить результат
            messages_resp = await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/messages",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v2"
                }
            )
            messages = messages_resp.json().get("data", [])

            assistant_reply = "🤖 Ошибка: ассистент не сгенерировал ответ."
            for msg in messages:
                if msg["role"] == "assistant" and "content" in msg:
                    for part in msg["content"]:
                        if part["type"] == "text":
                            assistant_reply = part["text"]["value"]
                            break
                    break

            # 6. Ответ в Telegram
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": assistant_reply}
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
