# helpdesk/integrations/llm.py
import base64

import frappe

# Seconds one model or embedding call may take when the setting is unset. The
# bot runs in a worker, and an unbounded call -- the Anthropic SDK defaults to
# ten minutes -- parks that worker for as long as the provider feels like
# taking. When a call runs out the bot escalates to a human, which is the
# right answer to a provider that is not answering.
DEFAULT_TIMEOUT_SECONDS = 30
MAX_RETRIES = 2

GEMINI_MODEL = "gemini-3.1-flash-lite"
ANTHROPIC_MODEL = "claude-haiku-4-5"


def _settings():
	return frappe.get_cached_doc("Helpdesk Bot Settings")


def request_timeout() -> int:
	"""Seconds any single model or embedding call may take."""
	try:
		value = int(_settings().get("llm_timeout_seconds") or 0)
	except Exception:
		value = 0
	return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def gemini_request_options() -> dict:
	"""`request_options` for the google.generativeai calls: a bounded wait."""
	return {"timeout": request_timeout()}


def anthropic_client(api_key: str):
	"""An Anthropic client with a bounded wait and the SDK's own backoff on 429/5xx."""
	import anthropic

	return anthropic.Anthropic(api_key=api_key, timeout=request_timeout(), max_retries=MAX_RETRIES)


def chat(messages: list[dict], images: list[bytes] | None = None, max_tokens: int = 512) -> str:
	"""Send messages to the configured LLM and return the reply string.

	Args:
		messages:   [{"role": "system"|"user"|"assistant", "content": str}, ...]
		images:     Optional raw image bytes attached to the last user message.
		max_tokens: Upper bound on response length (default 512 for bot replies;
		            pass 1024+ for longer generated content like KB articles).
	"""
	settings = _settings()
	provider = settings.llm_provider or GEMINI_MODEL
	if provider == GEMINI_MODEL:
		return _gemini(messages, images, settings, max_tokens)
	if provider == "claude-haiku-4-5":
		return _haiku(messages, images, settings, max_tokens)
	raise ValueError(f"Unknown LLM provider: {provider!r}")


def _gemini(messages: list[dict], images: list[bytes] | None, settings, max_tokens: int = 512) -> str:
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
		GEMINI_MODEL,
		system_instruction=system_text or None,
	)
	response = model.generate_content(
		contents,
		generation_config={"max_output_tokens": max_tokens},
		request_options=gemini_request_options(),
	)
	return response.text.strip()


def _haiku(messages: list[dict], images: list[bytes] | None, settings, max_tokens: int = 512) -> str:
	client = anthropic_client(settings.get_password("anthropic_api_key"))

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
		model=ANTHROPIC_MODEL,
		max_tokens=max_tokens,
		system=system_text or "You are a helpful support assistant.",
		messages=anthropic_messages,
	)
	return response.content[0].text.strip()
