const express = require("express");
const {
	makeWASocket, useMultiFileAuthState, DisconnectReason,
	makeCacheableSignalKeyStore, fetchLatestBaileysVersion, Browsers,
	downloadMediaMessage,
} = require("@whiskeysockets/baileys");
const pino = require("pino");
const { Boom } = require("@hapi/boom");
const axios = require("axios");
const qrcode = require("qrcode");

const fs           = require("fs");
const API_KEY      = process.env.API_KEY      || "changeme";
const WEBHOOK_URL  = process.env.WEBHOOK_URL  || "";
const SESSION_NAME = process.env.SESSION_NAME || "helpdesk";
const PORT         = parseInt(process.env.PORT || "3000");
const SESSION_DIR  = `/app/sessions/${SESSION_NAME}`;

// Derive the Frappe base URL from WEBHOOK_URL (e.g. http://frappe:8000)
const FRAPPE_BASE_URL = process.env.FRAPPE_BASE_URL || (WEBHOOK_URL ? new URL(WEBHOOK_URL).origin : "");
const UPLOAD_URL = FRAPPE_BASE_URL ? `${FRAPPE_BASE_URL}/api/method/helpdesk.integrations.baileys.upload_baileys_media` : "";

const logger = pino({ level: "info" });
let sock = null, qrString = null, isConnected = false;

// ── Contact LID→phone resolution ──────────────────────────────────────────────
// Maps @lid JIDs to phone numbers, populated from contacts.set / contacts.upsert.
const lidToPhone = {};   // e.g. "177893317574803@lid" → "254712345678"
const lidToName  = {};   // e.g. "177893317574803@lid" → "John Doe"
let lastContactsSample = [];   // first 10 raw contacts from the most recent contacts.set, for /contacts/debug

function normaliseJid(jid) {
	// Strip device suffix (:N) that appears in multi-device JIDs e.g. 254712345678:3@s.whatsapp.net
	return (jid || "").replace(/:\d+@/, "@");
}

function indexContacts(contacts) {
	let mapped = 0;
	// Keep a raw sample for the debug endpoint
	lastContactsSample = contacts.slice(0, 10);

	for (const c of contacts) {
		const rawId  = normaliseJid(c.id  || "");
		const rawLid = normaliseJid(c.lid || "");
		const name   = c.notify || c.name || c.verifiedName || "";

		let phoneJid = "", lidJid = "";

		if (rawId.endsWith("@s.whatsapp.net") && rawLid.endsWith("@lid")) {
			// Normal case: id=phone-JID, lid=@lid
			phoneJid = rawId; lidJid = rawLid;
		} else if (rawId.endsWith("@lid") && rawLid.endsWith("@s.whatsapp.net")) {
			// Reversed: id=@lid, lid=phone-JID (seen in some WA versions)
			phoneJid = rawLid; lidJid = rawId;
		} else if (rawId.endsWith("@s.whatsapp.net")) {
			// No paired @lid — index phone JID directly
			phoneJid = rawId;
		} else if (rawId.endsWith("@lid")) {
			// Only @lid, no phone mapping available
			lidJid = rawId;
		}

		const phone = phoneJid ? phoneJid.split("@")[0] : "";

		if (phone && lidJid) {
			lidToPhone[lidJid] = phone;
			if (name) lidToName[lidJid] = name;
			mapped++;
		}
		if (phone && name) lidToName[phoneJid] = name;
		if (!phone && lidJid && name) lidToName[lidJid] = name;
	}
	if (contacts.length > 0) {
		logger.info({ mapped, total: Object.keys(lidToPhone).length, nameCount: Object.keys(lidToName).length, sampleKeys: Object.keys(contacts[0] || {}) }, "contacts indexed");
	}
}

function resolvePhone(jid) {
	if (jid.endsWith("@s.whatsapp.net")) return jid.split("@")[0].split(":")[0];
	if (jid.endsWith("@lid") && lidToPhone[jid]) return lidToPhone[jid];
	return "";
}

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
		logger: pino({ level: "debug" }),
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
		} else if (connection === "open") { isConnected = true; qrString = null; logger.info("Connected"); }
	});

	// Full contact list on first sync
	sock.ev.on("contacts.set", ({ contacts }) => indexContacts(contacts));
	// Incremental contact updates
	sock.ev.on("contacts.upsert", (contacts) => indexContacts(contacts));
	// Messaging history (initial sync) — may include contact data
	sock.ev.on("messaging-history.set", ({ contacts }) => {
		if (contacts && contacts.length > 0) {
			logger.info({ count: contacts.length }, "messaging-history.set contacts");
			indexContacts(contacts);
		}
	});

	sock.ev.on("messages.upsert", async ({ messages, type }) => {
		if (type !== "notify" && type !== "append" || !WEBHOOK_URL) return;
		for (const msg of messages) {
			if (msg.key.fromMe || !msg.message) continue;
			const jid        = msg.key.remoteJid;
			const sender     = msg.key.participant || jid;
			const senderName = msg.pushName || lidToName[jid] || sender.split("@")[0];

			// Resolve phone for @lid contacts
			const resolvedPhone = resolvePhone(jid);

			// Unwrap view-once / ephemeral / document-with-caption containers
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

			logger.info({ jid, resolvedPhone: resolvedPhone || "(lid-unresolved)", contentType, type }, "Processing message");

			// Download and upload media for non-text/reaction messages
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

const app = express();
app.use(express.json());
const auth = (req, res, next) =>
	req.headers["x-api-key"] === API_KEY ? next() : res.status(401).json({ error: "Unauthorized" });

app.get("/health", (_, res) => {
	const rawId = sock?.user?.id || "";
	const phone = rawId ? rawId.split(":")[0].split("@")[0] : null;
	res.json({ connected: isConnected, hasQr: !!qrString, session: SESSION_NAME, phone });
});

app.get("/qr", async (_, res) => {
	if (isConnected) return res.json({ connected: true });
	if (!qrString)   return res.status(202).json({ waiting: true });
	const dataUrl = await qrcode.toDataURL(qrString);
	res.send(`<html><body style="background:#111;display:flex;align-items:center;justify-content:center;height:100vh">
		<img src="${dataUrl}" style="width:300px;height:300px"/></body></html>`);
});

// Returns all contacts in the LID→phone map for bulk sync
app.get("/contacts", auth, (_, res) => {
	const contacts = Object.entries(lidToPhone).map(([lid, phone]) => ({
		lid,
		phone,
		name: lidToName[lid] || "",
	}));
	// Also include @s.whatsapp.net contacts that have a name
	for (const [jid, name] of Object.entries(lidToName)) {
		if (jid.endsWith("@s.whatsapp.net")) {
			contacts.push({ lid: null, phone: jid.split("@")[0].split(":")[0], jid, name });
		}
	}
	res.json({ contacts, lidCount: Object.keys(lidToPhone).length });
});

// Debug: shows raw contact sample and current mapping state — helps diagnose empty lidToPhone
app.get("/contacts/debug", auth, async (_, res) => {
	let rawParticipantSample = null;
	if (isConnected) {
		try {
			const groups = await sock.groupFetchAllParticipating();
			const firstGroup = Object.values(groups)[0];
			if (firstGroup) {
				const meta = await sock.groupMetadata(firstGroup.id);
				rawParticipantSample = (meta.participants || []).slice(0, 3).map((p) => ({
					id: p.id, lid: p.lid, admin: p.admin, keys: Object.keys(p),
				}));
			}
		} catch (e) {
			rawParticipantSample = { error: e.message };
		}
	}
	res.json({
		lidCount: Object.keys(lidToPhone).length,
		nameCount: Object.keys(lidToName).length,
		sampleMappings: Object.entries(lidToPhone).slice(0, 5).map(([lid, phone]) => ({ lid, phone, name: lidToName[lid] || "" })),
		rawContactSample: lastContactsSample,
		rawParticipantSample,
	});
});

// Returns participants for a group JID, with resolved phone numbers
app.get("/groupParticipants", auth, async (req, res) => {
	if (!isConnected) return res.status(503).json({ error: "Not connected" });
	const { jid } = req.query;
	if (!jid) return res.status(400).json({ error: "jid required" });
	try {
		const meta = await sock.groupMetadata(jid);
		let newMappings = 0;
		const participants = (meta.participants || []).map((p) => {
			const rawId  = normaliseJid(p.id  || "");
			const rawLid = normaliseJid(p.lid || "");
			let phoneJid = "", lidJid = "";

			if (rawId.endsWith("@s.whatsapp.net") && rawLid.endsWith("@lid")) {
				phoneJid = rawId; lidJid = rawLid;
			} else if (rawId.endsWith("@lid") && rawLid.endsWith("@s.whatsapp.net")) {
				phoneJid = rawLid; lidJid = rawId;
			} else if (rawId.endsWith("@s.whatsapp.net")) {
				phoneJid = rawId;
			} else if (rawId.endsWith("@lid")) {
				lidJid = rawId;
			}

			const phone = phoneJid ? phoneJid.split("@")[0] : (lidJid ? lidToPhone[lidJid] || "" : "");
			const jidOut = lidJid || phoneJid || rawId;

			// Store any new lid→phone mappings discovered here
			if (phone && lidJid && !lidToPhone[lidJid]) {
				lidToPhone[lidJid] = phone;
				newMappings++;
			}

			const name = (lidJid ? lidToName[lidJid] : "") || (phoneJid ? lidToName[phoneJid] : "") || "";
			return { jid: jidOut, phone, name, isAdmin: p.admin === "admin" || p.admin === "superadmin" };
		});
		if (newMappings > 0) logger.info({ group: jid, newMappings }, "New lid→phone mappings from group participants");
		res.json({ groupName: meta.subject || "", participants });
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

app.get("/groups", auth, async (_, res) => {
	if (!isConnected) return res.status(503).json({ error: "Not connected" });
	try {
		const raw = await sock.groupFetchAllParticipating();
		const groups = Object.values(raw).map((g) => ({ jid: g.id, subject: g.subject, size: g.size || 0 }));
		res.json({ groups });
	} catch (err) { res.status(500).json({ error: err.message }); }
});

app.listen(PORT, () => { logger.info({ port: PORT }, "Gateway ready"); connectToWhatsApp(); });
