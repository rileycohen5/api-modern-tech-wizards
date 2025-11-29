import os
import requests

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")

def ask_llm(user_message: str, conversation_history=None):
    if conversation_history is None:
        conversation_history = []

    messages = [{"role": "system", "content": """
You are a helpful, friendly, natural-sounding voice assistant.
Keep responses under 2 sentences unless asked for details.
Talk like a human — warm, conversational, and helpful.
"""}]

    messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})

    url = f"{AZURE_OPENAI_ENDPOINT}/openai/deployments/{AZURE_OPENAI_DEPLOYMENT}/chat/completions?api-version=2024-02-15-preview"

    payload = {
        "messages": messages,
        "max_tokens": 150,
        "temperature": 0.8
    }

    headers = {
        "Content-Type": "application/json",
        "api-key": AZURE_OPENAI_API_KEY
    }

    response = requests.post(url, json=payload, headers=headers)
    response_json = response.json()

    reply = response_json["choices"][0]["message"]["content"]
    return reply
