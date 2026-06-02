# helpdesk/integrations/llm.py
import base64

import frappe


def _settings():
	return frappe.get_cached_doc("Helpdesk Bot Settings")


def chat(messages: list[dict], images: list[bytes] | None = None) -> str:
	"""Send messages to the configured LLM and return the reply string.

	Args:
		messages: [{"role": "system"|"user"|"assistant", "content": str}, ...]
		images:   Optional raw image bytes attached to the last user message.
	"""
	settings = _settings()
	provider = settings.llm_provider or "gemini-3.1-flash-lite"
	if provider == "gemini-3.1-flash-lite":
		return _gemini(messages, images, settings)
	if provider == "claude-haiku-4-5":
		return _haiku(messages, images, settings)
	raise ValueError(f"Unknown LLM provider: {provider!r}")


def _gemini(messages: list[dict], images: list[bytes] | None, settings) -> str:
	import google.generativeai as genai

	genai.configure(api_key=settings.get_password("gemini_api_key"))

	system_text = ""
	contents = []
	for msg in messages:
		if msg["role"] == "system":
			system_text = msg["content"]
			continue
		role = "user" if msg["role"] == "user" else "model"
		contents.append({"role": role, "parts": [{"text": msg["content"]}]})

	# Attach images to last user message
	if images:
		for i in range(len(contents) - 1, -1, -1):
			if contents[i]["role"] == "user":
				for img_bytes in images:
					contents[i]["parts"].append(
						{
							"inline_data": {
								"mime_type": "image/jpeg",
								"data": base64.b64encode(img_bytes).decode(),
							}
						}
					)
				break

	model = genai.GenerativeModel(
		"gemini-3.1-flash-lite",
		system_instruction=system_text or None,
	)
	response = model.generate_content(contents)
	return response.text.strip()


def _haiku(messages: list[dict], images: list[bytes] | None, settings) -> str:
	import anthropic

	client = anthropic.Anthropic(api_key=settings.get_password("anthropic_api_key"))

	system_text = ""
	anthropic_messages = []
	for msg in messages:
		if msg["role"] == "system":
			system_text = msg["content"]
			continue
		anthropic_messages.append({"role": msg["role"], "content": msg["content"]})

	# Attach images to last user message
	if images:
		for i in range(len(anthropic_messages) - 1, -1, -1):
			if anthropic_messages[i]["role"] == "user":
				parts = []
				for img_bytes in images:
					parts.append(
						{
							"type": "image",
							"source": {
								"type": "base64",
								"media_type": "image/jpeg",
								"data": base64.b64encode(img_bytes).decode(),
							},
						}
					)
				parts.append({"type": "text", "text": anthropic_messages[i]["content"]})
				anthropic_messages[i]["content"] = parts
				break

	response = client.messages.create(
		model="claude-haiku-4-5-20251001",
		max_tokens=512,
		system=system_text or "You are a helpful support assistant.",
		messages=anthropic_messages,
	)
	return response.content[0].text.strip()
