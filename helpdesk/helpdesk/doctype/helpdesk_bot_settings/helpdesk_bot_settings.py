import frappe
from frappe.model.document import Document


class HelpdeskBotSettings(Document):
	pass


@frappe.whitelist()
def test_connection():
	"""Send a minimal ping to the configured LLM and return a status dict."""
	settings = frappe.get_doc("Helpdesk Bot Settings")
	provider = settings.llm_provider or "Gemini Flash 2.0"

	ping = [
		{"role": "user", "content": "Reply with exactly the word: pong"},
	]

	try:
		if provider == "gemini-3.1-flash-lite":
			import google.generativeai as genai

			api_key = settings.get_password("gemini_api_key")
			if not api_key:
				return {"ok": False, "message": "Gemini API key is not set."}
			genai.configure(api_key=api_key)
			model = genai.GenerativeModel("gemini-3.1-flash-lite")
			response = model.generate_content([{"role": "user", "parts": [{"text": ping[0]["content"]}]}])
			reply = response.text.strip()

		elif provider == "claude-haiku-4-5":
			import anthropic

			api_key = settings.get_password("anthropic_api_key")
			if not api_key:
				return {"ok": False, "message": "Anthropic API key is not set."}
			client = anthropic.Anthropic(api_key=api_key)
			response = client.messages.create(
				model="claude-haiku-4-5-20251001",
				max_tokens=16,
				messages=[{"role": "user", "content": ping[0]["content"]}],
			)
			reply = response.content[0].text.strip()

		else:
			return {"ok": False, "message": f"Unknown provider: {provider}"}

		return {"ok": True, "message": f"Connected ({provider}). Model replied: {reply!r}"}

	except Exception as e:
		return {"ok": False, "message": str(e)}
