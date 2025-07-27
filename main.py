from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
import httpx
import os
import asyncio

app = FastAPI()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ASSISTANT_ID = os.environ.get("ASSISTANT_ID")

# Временное хранилище: chat_id -> thread_id
user_threads = {}

@app.post("/ask")
async def ask(request: Request):
    try:
        body = await request.json()
        user_text = body.get("text")
        chat_id = str(body.get("chat_id"))

        if not user_text or not chat_id:
            return JSONResponse(status_code=400, content={"error": "Missing text or chat_id"})

        async with httpx.AsyncClient() as client:

            # 1. Получить или создать thread_id
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

                if thread_resp.status_code != 200:
                    print("❌ Ошибка создания thread:", thread_resp.text)
                    return JSONResponse(status_code=500, content={"error": thread_resp.text})

                thread_data = thread_resp.json()
                thread_id = thread_data.get("id")
                if not thread_id:
                    return JSONResponse(status_code=500, content={"error": "No thread ID returned"})
                
                user_threads[chat_id] = thread_id
                print(f"🧵 Создан новый thread: {thread_id} для chat_id: {chat_id}")

            # 2. Отправить сообщение
            msg_resp = await client.post(
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

            if msg_resp.status_code != 200:
                print("❌ Ошибка отправки сообщения:", msg_resp.text)
                return JSONResponse(status_code=500, content={"error": msg_resp.text})

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

            if run_resp.status_code != 200:
                print("❌ Ошибка запуска run:", run_resp.text)
                return JSONResponse(status_code=500, content={"error": run_resp.text})

            run_data = run_resp.json()
            run_id = run_data.get("id")
            if not run_id:
                return JSONResponse(status_code=500, content={"error": "No run ID returned"})

            # 4. Ожидание завершения run
            for _ in range(30):
                run_status_resp = await client.get(
                    f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "OpenAI-Beta": "assistants=v2"
                    }
                )

                if run_status_resp.status_code != 200:
                    print("❌ Ошибка проверки статуса run:", run_status_resp.text)
                    return JSONResponse(status_code=500, content={"error": run_status_resp.text})

                run_status = run_status_resp.json()
                if run_status.get("status") == "completed":
                    break
                await asyncio.sleep(0.3)

            # 5. Получение сообщений
            messages_resp = await client.get(
                f"https://api.openai.com/v1/threads/{thread_id}/messages",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "assistants=v2"
                }
            )

            if messages_resp.status_code != 200:
                print("❌ Ошибка получения сообщений:", messages_resp.text)
                return JSONResponse(status_code=500, content={"error": messages_resp.text})

            messages = messages_resp.json().get("data", [])

            # DEBUG
            print("📨 THREAD MESSAGES:")
            for msg in messages:
                print(f" - role: {msg.get('role')}")
                print(f"   content: {msg.get('content')}")

            # 6. Извлечь ответ ассистента
            assistant_reply = "🤖 Ошибка: ассистент не сгенерировал текстовый ответ."
            for msg in messages:
                if msg.get("role") == "assistant":
                    for part in msg.get("content", []):
                        if part.get("type") == "text":
                            assistant_reply = part["text"]["value"]
                            break
                if assistant_reply != "🤖 Ошибка: ассистент не сгенерировал текстовый ответ.":
                    break

            # 7. Отправить в Telegram
            tg_resp = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": assistant_reply
                }
            )

            if tg_resp.status_code != 200:
                print("❌ Ошибка отправки в Telegram:", tg_resp.text)

        return {"status": "ok"}

    except Exception as e:
        print("❌ Общая ошибка:", str(e))
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
