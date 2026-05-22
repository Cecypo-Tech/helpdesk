import express from "express";
import baileys, {
	useMultiFileAuthState,
	DisconnectReason,
	makeCacheableSignalKeyStore,
	fetchLatestBaileysVersion,
	Browsers,
	downloadMediaMessage,
} from "@whiskeysockets/baileys";
import pino from "pino";
import { Boom } from "@hapi/boom";
import axios from "axios";
import qrcode from "qrcode";
import fs from "node:fs";

const makeWASocket = baileys.default || baileys;

const API_KEY      = process.env.API_KEY      || "changeme";
const WEBHOOK_URL  = process.env.WEBHOOK_URL  || "";
const SESSION_NAME = process.env.SESSION_NAME || "helpdesk";
const PORT         = parseInt(process.env.PORT || "3000");
const SESSION_DIR  = `/app/sessions/${SESSION_NAME}`;

const FRAPPE_BASE_URL    = process.env.FRAPPE_BASE_URL || (WEBHOOK_URL ? new URL(WEBHOOK_URL).origin : "");
const UPLOAD_URL         = FRAPPE_BASE_URL ? `${FRAPPE_BASE_URL}/api/method/helpdesk.integrations.baileys.upload_baileys_media` : "";
const CONTACT_UPSERT_URL = FRAPPE_BASE_URL ? `${FRAPPE_BASE_URL}/api/method/helpdesk.integrations.baileys.upsert_contact_mapping` : "";

const logger = pino({ level: "info" });
let sock = null, qrString = null, isConnected = false;

// ── Helpers ──────────────────────────────────────────────────────────────────

function digits(jidOrPhone) {
	if (!jidOrPhone) return "";
	return String(jidOrPhone).split("@")[0].split(":")[0].replace(/\D/g, "");
}

// In 7.x message keys carry remoteJidAlt/participantAlt with the LID↔PN counterpart.
// Returns { lid, pn } where each is a fully-qualified JID or "".
function pairFromKey(primaryJid, altJid) {
	const p = primaryJid || "", a = altJid || "";
	if (p.endsWith("@lid")) return { lid: p, pn: a.endsWith("@s.whatsapp.net") ? a : "" };
	if (p.endsWith("@s.whatsapp.net")) return { pn: p, lid: a.endsWith("@lid") ? a : "" };
	return { lid: "", pn: "" };
}

// 7.x Contact shape: { id (preferred), lid?, phoneNumber?, name?, notify?, verifiedName? }
function contactToMapping(c) {
	const id = c?.id || "";
	const lid = c?.lid || (id.endsWith("@lid") ? id : "");
	const phoneJid = c?.phoneNumber || (id.endsWith("@s.whatsapp.net") ? id : "");
	return {
		lid,
		phone: digits(phoneJid),
		name: c?.notify || c?.name || c?.verifiedName || "",
	};
}

// Fire-and-forget push to Frappe. Stays small (no retries) — message webhook is the
// reliability boundary; if a single mapping push fails the next message will retry.
async function pushContactMapping({ lid, phone, name }) {
	if (!CONTACT_UPSERT_URL) return;
	const payload = { lid: lid || "", phone: digits(phone || ""), name: name || "" };
	if (!payload.lid && !payload.phone) return;
	try {
		await axios.post(CONTACT_UPSERT_URL, payload, {
			headers: { "X-API-Key": API_KEY },
			timeout: 5000,
		});
	} catch (err) {
		logger.trace({ err: err.message, ...payload }, "Contact upsert failed");
	}
}

async function pushContactMappings(mappings) {
	const seen = new Set();
	for (const m of mappings) {
		const key = `${m.lid}|${digits(m.phone)}`;
		if (seen.has(key)) continue;
		seen.add(key);
		await pushContactMapping(m);
	}
}

// Push a group's subject as a Baileys Contact custom_name keyed by the group JID.
// Same endpoint as contact mappings; backend routes on the @g.us suffix.
async function pushGroupName(jid, name) {
	if (!CONTACT_UPSERT_URL || !jid || !jid.endsWith("@g.us") || !name) return;
	try {
		await axios.post(CONTACT_UPSERT_URL, { jid, name }, {
			headers: { "X-API-Key": API_KEY },
			timeout: 5000,
		});
	} catch (err) {
		logger.trace({ err: err.message, jid }, "Group name upsert failed");
	}
}

// Resolve a phone number for any JID. Order:
// 1. JID is already a PN → strip the digits
// 2. lidMapping store has a cached/persisted reverse mapping → use it
// 3. Otherwise return ""
async function resolvePhone(jid) {
	if (!jid) return "";
	if (jid.endsWith("@s.whatsapp.net")) return digits(jid);
	if (jid.endsWith("@lid")) {
		try {
			const pn = await sock?.signalRepository?.lidMapping?.getPNForLID(jid);
			if (pn) return digits(pn);
		} catch (err) {
			logger.trace({ err: err.message, jid }, "getPNForLID failed");
		}
	}
	return "";
}

// ── Connection ───────────────────────────────────────────────────────────────

async function connectToWhatsApp() {
	const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
	const { version } = await fetchLatestBaileysVersion();
	logger.info({ version }, "Using WA version");

	sock = makeWASocket({
		version,
		browser: Browsers.ubuntu("Chrome"),
		auth: {
			creds: state.creds,
			keys: makeCacheableSignalKeyStore(state.keys, pino({ level: "silent" })),
		},
		logger: pino({ level: "warn" }),
	});

	sock.ev.on("creds.update", saveCreds);

	sock.ev.on("connection.update", ({ connection, lastDisconnect, qr }) => {
		if (qr) { qrString = qr; isConnected = false; logger.info("QR code ready"); }
		logger.info({ connection, qr: !!qr }, "connection.update");
		if (connection === "close") {
			isConnected = false; qrString = null;
			const code = lastDisconnect?.error instanceof Boom ? lastDisconnect.error.output.statusCode : null;
			logger.warn({ code, reason: lastDisconnect?.error?.message }, "Connection closed");
			if (code === DisconnectReason.loggedOut || code === 405) {
				logger.warn({ code }, "Clearing stale session for fresh QR");
				fs.rmSync(SESSION_DIR, { recursive: true, force: true });
				setTimeout(connectToWhatsApp, 3000);
			} else {
				setTimeout(connectToWhatsApp, 5000);
			}
		} else if (connection === "open") {
			isConnected = true; qrString = null;
			logger.info("Connected");
			bootstrapContactSync().catch((e) => logger.warn({ err: e.message }, "Bootstrap sync failed"));
		}
	});

	// 7.x emits an explicit lidPnMappings array on history sync — a free bulk feed.
	sock.ev.on("messaging-history.set", ({ contacts, lidPnMappings }) => {
		const mappings = [];
		for (const m of (lidPnMappings || [])) {
			mappings.push({ lid: m.lid, phone: digits(m.pn), name: "" });
		}
		for (const c of (contacts || [])) {
			mappings.push(contactToMapping(c));
		}
		if (mappings.length) {
			logger.info({ count: mappings.length }, "messaging-history.set → pushing mappings");
			pushContactMappings(mappings).catch(() => {});
		}
	});

	// Incremental contact metadata — display names mostly. Also occasionally carries
	// LID↔PN pairs for newly-known contacts.
	sock.ev.on("contacts.upsert", (contacts) => {
		const mappings = (contacts || []).map(contactToMapping).filter((m) => m.lid || m.phone);
		if (mappings.length) {
			logger.info({ count: mappings.length }, "contacts.upsert → pushing mappings");
			pushContactMappings(mappings).catch(() => {});
		}
	});

	// Live group subject changes: WA fires groups.upsert when we join/discover a group
	// and groups.update on rename. Both deliver { id, subject? }.
	sock.ev.on("groups.upsert", (groups) => {
		for (const g of (groups || [])) {
			if (g?.id && g?.subject) pushGroupName(g.id, g.subject).catch(() => {});
		}
	});
	sock.ev.on("groups.update", (updates) => {
		for (const g of (updates || [])) {
			if (g?.id && g?.subject) pushGroupName(g.id, g.subject).catch(() => {});
		}
	});

	sock.ev.on("messages.upsert", async ({ messages, type }) => {
		if ((type !== "notify" && type !== "append") || !WEBHOOK_URL) return;

		for (const msg of messages) {
			if (msg.key.fromMe || !msg.message) continue;

			const jid       = msg.key.remoteJid;
			const isGroup   = jid?.endsWith("@g.us");
			const sender    = msg.key.participant || jid;
			const senderName = msg.pushName || sender?.split("@")[0] || "";

			// Mine LID↔PN pairs straight from the message key — free, instant, accurate.
			const chatPair = isGroup ? { lid: "", pn: "" } : pairFromKey(jid, msg.key.remoteJidAlt);
			const partPair = isGroup ? pairFromKey(sender, msg.key.participantAlt) : { lid: "", pn: "" };

			const learned = [];
			if (chatPair.lid || chatPair.pn) {
				learned.push({ lid: chatPair.lid, phone: digits(chatPair.pn), name: isGroup ? "" : senderName });
			}
			if (partPair.lid || partPair.pn) {
				learned.push({ lid: partPair.lid, phone: digits(partPair.pn), name: senderName });
			}
			if (learned.length) pushContactMappings(learned).catch(() => {});

			// Resolve the phone shipped on the webhook. Prefer the alt PN we already have;
			// fall back to the signalRepository lid-mapping store; final fallback: "".
			const resolvedPhone = isGroup
				? (digits(partPair.pn) || await resolvePhone(sender))
				: (digits(chatPair.pn) || await resolvePhone(jid));

			// Unwrap container messages (view-once, ephemeral, documentWithCaption)
			const mc = msg.message?.viewOnceMessage?.message
				|| msg.message?.viewOnceMessageV2?.message?.viewOnceMessage?.message
				|| msg.message?.ephemeralMessage?.message
				|| msg.message?.documentWithCaptionMessage?.message
				|| msg.message;

			let text = "", contentType = "text", quotedMessageId = "";

			if (mc.conversation) {
				text = mc.conversation;
			} else if (mc.extendedTextMessage) {
				text = mc.extendedTextMessage.text;
				quotedMessageId = mc.extendedTextMessage.contextInfo?.stanzaId || "";
			} else if (mc.imageMessage) {
				contentType = "image"; text = mc.imageMessage.caption || "";
				quotedMessageId = mc.imageMessage.contextInfo?.stanzaId || "";
			} else if (mc.videoMessage) {
				contentType = "video"; text = mc.videoMessage.caption || "";
				quotedMessageId = mc.videoMessage.contextInfo?.stanzaId || "";
			} else if (mc.audioMessage) {
				contentType = "audio";
				quotedMessageId = mc.audioMessage.contextInfo?.stanzaId || "";
			} else if (mc.documentMessage) {
				contentType = "document"; text = mc.documentMessage.caption || "";
				quotedMessageId = mc.documentMessage.contextInfo?.stanzaId || "";
			} else if (mc.reactionMessage) {
				contentType = "reaction";
				text = mc.reactionMessage.text || "";
				quotedMessageId = mc.reactionMessage.key?.id || "";
			} else {
				logger.info({ jid, keys: Object.keys(mc) }, "Unhandled message type — skipping");
				continue;
			}

			logger.info({ jid, resolvedPhone: resolvedPhone || "(unresolved)", contentType, type }, "Processing message");

			let mediaUrl = "";
			if (contentType !== "text" && contentType !== "reaction" && UPLOAD_URL) {
				try {
					const buffer = await downloadMediaMessage(msg, "buffer", {});
					const ext = contentType === "image" ? "jpg"
						: contentType === "video" ? "mp4"
						: contentType === "audio" ? "ogg"
						: (mc.documentMessage?.fileName?.split(".").pop() || "bin");
					const filename = mc.documentMessage?.fileName || `wa_${msg.key.id}.${ext}`;
					const contentB64 = buffer.toString("base64");
					const uploadRes = await axios.post(UPLOAD_URL, {
						filename, content_b64: contentB64,
					}, { headers: { "X-API-Key": API_KEY }, timeout: 30000 });
					mediaUrl = uploadRes.data?.message?.file_url || uploadRes.data?.file_url || "";
					logger.info({ jid, contentType, mediaUrl }, "Media uploaded");
				} catch (err) {
					logger.warn({ err: err.message, jid, contentType }, "Media upload failed — sending without URL");
				}
			}

			try {
				await axios.post(WEBHOOK_URL, {
					jid, messageId: msg.key.id, sender, senderName,
					message: text, contentType, timestamp: msg.messageTimestamp,
					quotedMessageId, mediaUrl, resolvedPhone,
				}, { headers: { "X-API-Key": API_KEY }, timeout: 15000 });
			} catch (err) { logger.error({ err: err.message, jid }, "Webhook failed"); }
		}
	});
}

// One-shot walk: enumerate groups, read participant metadata, push everything to Frappe.
// Runs on every successful connection.open and on demand via POST /resync.
async function bootstrapContactSync() {
	if (!sock || !isConnected) return { groups: 0, mappings: 0 };
	const groups = await sock.groupFetchAllParticipating();
	const list = Object.values(groups);

	const mappings = [];
	let names = 0;
	for (const g of list) {
		try {
			const meta = await sock.groupMetadata(g.id);
			if (meta.subject) {
				pushGroupName(g.id, meta.subject).catch(() => {});
				names++;
			}
			for (const p of (meta.participants || [])) {
				const m = contactToMapping(p);
				if (m.lid || m.phone) mappings.push(m);
			}
		} catch (err) {
			logger.trace({ err: err.message, group: g.id }, "groupMetadata failed");
		}
	}

	if (mappings.length) await pushContactMappings(mappings);
	logger.info({ groups: list.length, names, mappings: mappings.length }, "Bootstrap contact sync complete");
	return { groups: list.length, names, mappings: mappings.length };
}

// ── HTTP API ─────────────────────────────────────────────────────────────────

const app = express();
app.use(express.json({ limit: "50mb" }));
const auth = (req, res, next) =>
	req.headers["x-api-key"] === API_KEY ? next() : res.status(401).json({ error: "Unauthorized" });

app.get("/health", (_, res) => {
	const rawId = sock?.user?.id || "";
	const phone = rawId ? digits(rawId) : null;
	res.json({ connected: isConnected, hasQr: !!qrString, session: SESSION_NAME, phone });
});

app.get("/qr", async (_, res) => {
	if (isConnected) return res.json({ connected: true });
	if (!qrString)   return res.status(202).json({ waiting: true });
	const dataUrl = await qrcode.toDataURL(qrString);
	res.send(`<html><body style="background:#111;display:flex;align-items:center;justify-content:center;height:100vh">
		<img src="${dataUrl}" style="width:300px;height:300px"/></body></html>`);
});

// Trigger a contact resync — walks groups, pushes participant mappings to Frappe.
app.post("/resync", auth, async (_, res) => {
	if (!isConnected) return res.status(503).json({ error: "Not connected" });
	try {
		const stats = await bootstrapContactSync();
		res.json({ ok: true, ...stats });
	} catch (err) { res.status(500).json({ error: err.message }); }
});

// Returns participants for a group JID. Mappings are pushed to Frappe as a side effect.
app.get("/groupParticipants", auth, async (req, res) => {
	if (!isConnected) return res.status(503).json({ error: "Not connected" });
	const { jid } = req.query;
	if (!jid) return res.status(400).json({ error: "jid required" });
	try {
		const meta = await sock.groupMetadata(jid);
		const mappings = [];
		const participants = (meta.participants || []).map((p) => {
			const m = contactToMapping(p);
			if (m.lid || m.phone) mappings.push(m);
			return {
				jid: p.id,
				phone: m.phone,
				name: m.name,
				isAdmin: p.admin === "admin" || p.admin === "superadmin",
			};
		});
		if (mappings.length) pushContactMappings(mappings).catch(() => {});
		res.json({ groupName: meta.subject || "", participants });
	} catch (err) { res.status(500).json({ error: err.message }); }
});

app.get("/groups", auth, async (_, res) => {
	if (!isConnected) return res.status(503).json({ error: "Not connected" });
	try {
		const raw = await sock.groupFetchAllParticipating();
		const groups = Object.values(raw).map((g) => ({ jid: g.id, subject: g.subject, size: g.size || 0 }));
		res.json({ groups });
	} catch (err) { res.status(500).json({ error: err.message }); }
});

app.post("/send", auth, async (req, res) => {
	if (!isConnected) return res.status(503).json({ error: "Not connected" });
	const { jid, message, mediaUrl, contentType = "text", replyToMessageId, replyToText, replyToFromMe, mentionedJids } = req.body;
	if (!jid || (!message && !mediaUrl)) return res.status(400).json({ error: "jid and message/mediaUrl required" });
	try {
		let content;
		if (!mediaUrl || contentType === "text") {
			content = { text: message };
		} else if (contentType === "image") {
			content = { image: { url: mediaUrl }, caption: message || "" };
		} else if (contentType === "video") {
			content = { video: { url: mediaUrl }, caption: message || "" };
		} else if (contentType === "audio") {
			content = { audio: { url: mediaUrl }, mimetype: "audio/mp4" };
		} else {
			content = { document: { url: mediaUrl }, fileName: message || "file" };
		}
		if (Array.isArray(mentionedJids) && mentionedJids.length) {
			content.mentions = mentionedJids;
		}
		const options = {};
		if (replyToMessageId) {
			options.quoted = {
				key: { remoteJid: jid, id: replyToMessageId, fromMe: replyToFromMe ?? false },
				message: { conversation: replyToText || "" },
			};
		}
		const sent = await sock.sendMessage(jid, content, options);
		res.json({ messageId: sent.key.id });
	} catch (err) { res.status(500).json({ error: err.message }); }
});

app.post("/markRead", auth, async (req, res) => {
	if (!isConnected) return res.status(503).json({ error: "Not connected" });
	const { jid, messageIds } = req.body;
	if (!jid || !Array.isArray(messageIds)) return res.status(400).json({ error: "jid and messageIds[] required" });
	try {
		await sock.readMessages(messageIds.map((id) => ({ remoteJid: jid, id, fromMe: false })));
		res.json({ ok: true });
	} catch (err) { res.status(500).json({ error: err.message }); }
});

app.post("/react", auth, async (req, res) => {
	if (!isConnected) return res.status(503).json({ error: "Not connected" });
	const { jid, messageId, emoji, fromMe } = req.body;
	if (!jid || !messageId || emoji === undefined) return res.status(400).json({ error: "jid, messageId and emoji required" });
	try {
		await sock.sendMessage(jid, {
			react: { text: emoji ?? "", key: { remoteJid: jid, id: messageId, fromMe: fromMe ?? false } },
		});
		res.json({ ok: true });
	} catch (err) { res.status(500).json({ error: err.message }); }
});

app.listen(PORT, () => { logger.info({ port: PORT }, "Gateway ready"); connectToWhatsApp(); });
