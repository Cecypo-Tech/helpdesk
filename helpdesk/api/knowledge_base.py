import frappe
from bs4 import BeautifulSoup
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import get_user_info_for_avatar

from helpdesk.utils import agent_only, is_agent


@frappe.whitelist(allow_guest=True)
def get_article(name: str):
    article = frappe.get_doc("HD Article", name).as_dict()

    if not is_agent() and (
        article["status"] != "Published" or article.get("internal")
    ):
        frappe.throw(_("Access denied"), frappe.PermissionError)

    author = get_user_info_for_avatar(article["author"])
    feedback = (
        frappe.db.get_value(
            "HD Article Feedback",
            {"article": name, "user": frappe.session.user},
            "feedback",
        )
        or 0
    )

    return {
        "name": article.name,
        "title": article.title,
        "content": article.content,
        "author": author,
        "creation": article.creation,
        "status": article.status,
        "published_on": article.published_on,
        "modified": article.modified,
        "category_name": frappe.db.get_value(
            "HD Article Category", article.category, "category_name"
        ),
        "category_id": article.category,
        "feedback": int(feedback),
        # Agents only. Product tags are routing metadata for the support bot,
        # not something the customer portal needs, and get_article allows guests.
        "products": article_products(name) if is_agent() else [],
    }

    return article


@frappe.whitelist()
def delete_articles(articles: list[str]):
    for article in articles:
        frappe.delete_doc("HD Article", article)


@frappe.whitelist()
def create_category(title: str):
    category = frappe.new_doc("HD Article Category", category_name=title).insert()
    article = frappe.new_doc(
        "HD Article", title="New Article", category=category.name
    ).insert()
    return {"article": article.name, "category": category.name}


@frappe.whitelist()
def move_to_category(category: str, articles: list[str]):
    frappe.has_permission("HD Article", "write", throw=True)

    for article in articles:
        try:
            article_category = frappe.db.get_value("HD Article", article, "category")
            category_existing_articles = frappe.db.count(
                "HD Article", {"category": article_category}
            )
            if category_existing_articles == 1:
                frappe.throw(_("Category must have atleast one article"))
                return
            else:
                frappe.db.set_value(
                    "HD Article", article, "category", category, update_modified=False
                )
        except Exception as e:
            frappe.db.rollback()
            frappe.throw(_("Error moving article to category"))


@frappe.whitelist()
def get_categories():
    categories = frappe.get_all(
        "HD Article Category",
        fields=["name", "category_name", "modified"],
    )
    for c in categories:
        filters = {"category": c.name, "status": "Published"}
        if not is_agent():
            filters["internal"] = 0
        c["article_count"] = frappe.db.count("HD Article", filters=filters)

    categories.sort(key=lambda c: c["article_count"], reverse=True)
    categories = [c for c in categories if c["article_count"] > 0]
    return categories


@frappe.whitelist()
def get_category_articles(category: str):
    filters = {"category": category, "status": "Published"}
    if not is_agent():
        filters["internal"] = 0
    articles = frappe.get_all(
        "HD Article",
        filters=filters,
        fields=["name", "title", "published_on", "modified", "author", "content", "source_url"],
    )
    for article in articles:
        article["author"] = get_user_info_for_avatar(article["author"])
        soup = BeautifulSoup(article["content"], "html.parser")
        article["content"] = str(soup.text)[:100]

    return articles


@frappe.whitelist()
def merge_category(source: str, target: str):
    frappe.has_permission("HD Article Category", "delete", throw=True)

    if source == target:
        frappe.throw(_("Source and target category cannot be same"))
    general_category = get_general_category()
    if source == general_category:
        frappe.throw(_("Cannot merge General category"))
    source_articles = frappe.get_all(
        "HD Article",
        filters={"category": source},
        pluck="name",
    )
    for article in source_articles:
        frappe.db.set_value(
            "HD Article", article, "category", target, update_modified=False
        )

    frappe.delete_doc("HD Article Category", source)


@frappe.whitelist()
def get_general_category():
    return frappe.db.get_value(
        "HD Article Category", {"category_name": "General"}, "name"
    )


@frappe.whitelist()
def get_category_title(category: str):
    return frappe.db.get_value("HD Article Category", category, "category_name")


@frappe.whitelist()
@rate_limit(key="article", seconds=60 * 60)
def increment_views(article: str):
    views = frappe.db.get_value("HD Article", article, "views") or 0
    views += 1
    frappe.db.set_value("HD Article", article, "views", views, update_modified=False)


def article_products(article: str) -> list[str]:
	"""Product names this article is tagged with.

	Wrapped because `products` is a Custom Field: on a site that has not
	migrated since the fixtures landed the child table does not exist and Frappe
	raises OperationalError. Returning [] there reads the article as generic,
	which keeps the bot answering — the same fail-open rule the scoping uses.
	"""
	try:
		return frappe.get_all(
			"HD Article Product",
			filters={"parent": article, "parenttype": "HD Article"},
			pluck="product",
			order_by="idx asc",
		)
	except Exception:
		return []


@frappe.whitelist()
@agent_only
def set_article_products(article: str, products: list[str] | None = None):
	"""Replace an article's product tags.

	Replace, not merge: an empty list is a deliberate action that makes the
	article generic again — untagged articles answer for every product — so it
	must be possible to clear tags, not only add them.
	"""
	if isinstance(products, str):
		products = frappe.parse_json(products)
	products = products or []

	seen = []
	for product in products:
		if product in seen:
			continue
		if not frappe.db.exists("HD Product", product):
			frappe.throw(_("Unknown product: {0}").format(product))
		seen.append(product)

	doc = frappe.get_doc("HD Article", article)
	doc.set("products", [])
	for product in seen:
		doc.append("products", {"product": product})
	doc.save(ignore_permissions=True)

	return seen
