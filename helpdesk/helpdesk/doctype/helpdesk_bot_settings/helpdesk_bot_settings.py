import frappe
from frappe.model.document import Document


class HelpdeskBotSettings(Document):
	pass


@frappe.whitelist()
def test_chat(message: str, history: str = "[]") -> dict:
	"""Run the full bot pipeline on a message and return the reply + KB articles used.

	history: JSON-encoded list of {role, content} dicts for multi-turn context.
	"""
	import json
	from helpdesk.integrations.bot import _combined_kb_search
	from helpdesk.integrations.llm import chat as llm_chat

	settings = frappe.get_doc("Helpdesk Bot Settings")
	if not settings.get_password("gemini_api_key") and not settings.get_password("anthropic_api_key"):
		return {"ok": False, "reply": "No API key configured.", "articles": []}

	try:
		prior = json.loads(history) if isinstance(history, str) else history
	except Exception:
		prior = []

	articles = _combined_kb_search(message, settings.kb_search_limit or 3)
	kb_context = "\n\n".join(f"Article: {a['title']}\n{a['content']}" for a in articles)
	system_content = settings.system_prompt or "You are a helpful support assistant."
	if kb_context:
		system_content += f"\n\nKnowledge Base:\n{kb_context}"
	if prior:
		system_content += (
			"\n\nOVERRIDE — FOLLOW-UP RULE (takes precedence over all other instructions): "
			"The conversation history shows prior exchanges. If the user's current message asks you to "
			"reformat, filter, modify, or clarify your PREVIOUS response (examples: 'remove the expiry date', "
			"'show only names', 'make it a table', 'without the key column') you MUST fulfil that request "
			"using your previous response as the source. Do NOT say you lack information. "
			"This rule overrides the KB-only restriction."
		)

	messages = [{"role": "system", "content": system_content}]
	messages.extend(prior)
	messages.append({"role": "user", "content": message})

	try:
		reply = llm_chat(messages)
	except Exception as e:
		return {"ok": False, "reply": str(e), "articles": []}

	return {
		"ok": True,
		"reply": reply,
		"articles": [{"title": a["title"]} for a in articles],
	}


@frappe.whitelist()
def test_connection():
	"""Send a minimal ping to the configured LLM and return a status dict."""
	from helpdesk.integrations import llm

	settings = frappe.get_doc("Helpdesk Bot Settings")
	provider = settings.llm_provider or llm.GEMINI_MODEL

	ping = [
		{"role": "user", "content": "Reply with exactly the word: pong"},
	]

	try:
		if provider == llm.GEMINI_MODEL:
			import google.generativeai as genai

			api_key = settings.get_password("gemini_api_key")
			if not api_key:
				return {"ok": False, "message": "Gemini API key is not set."}
			genai.configure(api_key=api_key)
			model = genai.GenerativeModel(llm.GEMINI_MODEL)
			response = model.generate_content(
				ping[0]["content"], request_options=llm.gemini_request_options()
			)
			reply = response.text.strip()

		elif provider == "claude-haiku-4-5":
			api_key = settings.get_password("anthropic_api_key")
			if not api_key:
				return {"ok": False, "message": "Anthropic API key is not set."}
			client = llm.anthropic_client(api_key)
			response = client.messages.create(
				model=llm.ANTHROPIC_MODEL,
				max_tokens=16,
				messages=[{"role": "user", "content": ping[0]["content"]}],
			)
			reply = response.content[0].text.strip()

		else:
			return {"ok": False, "message": f"Unknown provider: {provider}"}

		return {"ok": True, "message": f"Connected ({provider}). Model replied: {reply!r}"}

	except Exception as e:
		return {"ok": False, "message": str(e)}
