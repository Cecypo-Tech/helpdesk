import frappe


def promote_gaps_to_draft_articles() -> None:
	"""Daily scheduled job: convert pending KB gap records into draft HD Articles."""
	try:
		if not frappe.get_cached_doc("Helpdesk Bot Settings").is_enabled:
			return
	except Exception:
		return

	gaps = frappe.db.get_all(
		"HD Bot Missing KB Query",
		filters={"status": "Pending"},
		fields=["name", "query_text", "suggested_title", "suggested_category"],
		order_by="creation asc",
		limit=10,
	)
	if not gaps:
		return

	from helpdesk.integrations.llm import chat as llm_chat

	for gap in gaps:
		if not gap.query_text:
			frappe.db.set_value("HD Bot Missing KB Query", gap.name, "status", "Dismissed")
			frappe.db.commit()
			continue

		title = (gap.suggested_title or gap.query_text)[:100]

		# Reuse an existing draft with the same title rather than creating a duplicate
		existing = frappe.db.get_value(
			"HD Article", {"title": title, "status": "Draft"}, "name"
		)
		if existing:
			frappe.db.set_value(
				"HD Bot Missing KB Query",
				gap.name,
				{"status": "Article Created", "created_article": existing},
			)
			frappe.db.commit()
			continue

		try:
			messages = [
				{
					"role": "system",
					"content": (
						"You are a technical writer for a customer support knowledge base. "
						"Write a concise, helpful support article based on the customer query provided. "
						"Use markdown formatting. Include: a brief explanation, step-by-step instructions "
						"if applicable, and a short closing note. "
						"Do NOT include a top-level H1 heading — the title is stored separately. "
						"Keep it under 350 words."
					),
				},
				{
					"role": "user",
					"content": (
						f"Customer query: {gap.query_text}\n"
						f"Article title: {title}\n"
						f"Category: {gap.suggested_category or 'General'}"
					),
				},
			]
			content = llm_chat(messages, max_tokens=1024)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"KB Autofill: LLM failed for gap {gap.name}")
			continue

		# Resolve category name → docname link
		category = None
		if gap.suggested_category:
			category = frappe.db.get_value(
				"HD Article Category",
				{"category_name": gap.suggested_category},
				"name",
			)

		try:
			article = frappe.get_doc({
				"doctype": "HD Article",
				"title": title,
				"content": content,
				"status": "Draft",
				"category": category,
				"internal": 0,
			})
			article.insert(ignore_permissions=True)
			frappe.db.set_value(
				"HD Bot Missing KB Query",
				gap.name,
				{"status": "Article Created", "created_article": article.name},
			)
			frappe.db.commit()
			frappe.logger().info(f"KB Autofill: created draft article '{title}' from gap {gap.name}")
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"KB Autofill: article insert failed for gap {gap.name}")
