from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi import status
import httpx
import os
import asyncio
import json
import traceback

app = FastAPI()

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ASSISTANT_ID = os.environ["ASSISTANT_ID"]
NOTION_PROXY_URL = os.environ.get("NOTION_PROXY_URL", "http://localhost:8001")  # Поддержка внешнего адреса

user_threads = {}  # chat_id -> thread_id

@app.post("/ask")
async def ask(request: Request):
    try:
        body = await request.json()
        user_text = body["text"]
        chat_id = str(body["chat_id"])

        print(f"📨 Получен запрос от {chat_id}: {user_text}")

        async with httpx.AsyncClient() as client:
            # 1. Получить или создать thread
            if chat_id in user_threads:
                thread_id = user_threads[chat_id]
                print(f"🔁 Используем существующий thread: {thread_id}")
            else:
                print(f"➕ Создаём новый thread для chat_id: {chat_id}")
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
                print(f"✅ Новый thread: {thread_id}")

            # 2. Отправка сообщения
            print("📤 Отправляем сообщение ассистенту...")
            await client.post(
                f"https://api.openai.com/v1/threads/{thread_id}/messages",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v2",
                    "Content-Type": "application/json"
                },
                json={"role": "user", "content": user_text}
            )

            # 3. Запуск run
            print("▶️ Запускаем run...")
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
            run_id = run["id"]
            print(f"🚦 Run ID: {run_id}")

            # 4. Ждём завершения run или actions
            for i in range(30):
                run_status_resp = await client.get(
                    f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "OpenAI-Beta": "assistants=v2"
                    }
                )
                run_status = run_status_resp.json()
                status_ = run_status["status"]
                print(f"⏳ [{i}] Статус run: {status_}")

                if status_ in ["completed", "failed"]:
                    print(f"✅ Run завершён: {status_}")
                    break

                elif status_ == "requires_action":
                    print("🛠 Требуются действия: запускаем tool calls...")
                    tool_calls = run_status["required_action"]["submit_tool_outputs"]["tool_calls"]

                    tool_outputs = []
                    for call in tool_calls:
                        function_name = call["function"]["name"]
                        arguments = json.loads(call["function"]["arguments"])
                        print(f"🔧 Вызов функции: {function_name} с аргументами: {arguments}")

                        try:
                            notion_response = await client.post(
                                f"{NOTION_PROXY_URL}/{function_name}",
                                json=arguments,
                                timeout=10.0
                            )
                            notion_response.raise_for_status()
                            notion_result = notion_response.json()
                            print(f"✅ Ответ от прокси: {notion_result}")
                        except Exception as e:
                            print(f"❌ Ошибка вызова прокси-функции {function_name}: {e}")
                            traceback.print_exc()
                            notion_result = {"error": str(e)}

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

            # 5. Получаем ответ
            print("📩 Получаем сообщения...")
            messages_resp = await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/messages",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v2"
                }
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

            print(f"📨 Ответ ассистента: {assistant_reply}")

            # 6. Ответ в Telegram
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": assistant_reply}
            )

        return {"status": "ok"}

    except Exception as e:
        print("❌ ERROR:", str(e))
        traceback.print_exc()
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
