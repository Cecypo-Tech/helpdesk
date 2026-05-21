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
				// 401 = logged out deliberately; 405 = server rejected session — both need fresh auth
				logger.warn({ code }, "Clearing stale session for fresh QR");
				fs.rmSync(SESSION_DIR, { recursive: true, force: true });
				setTimeout(connectToWhatsApp, 3000);
			} else {
				setTimeout(connectToWhatsApp, 5000);
			}
		} else if (connection === "open") { isConnected = true; qrString = null; logger.info("Connected"); }
	});
	sock.ev.on("messages.upsert", async ({ messages, type }) => {
		if (type !== "notify" && type !== "append" || !WEBHOOK_URL) return;
		for (const msg of messages) {
			if (msg.key.fromMe || !msg.message) continue;
			const jid        = msg.key.remoteJid;
			const sender     = msg.key.participant || jid;
			const senderName = msg.pushName || sender.split("@")[0];

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

			logger.info({ jid, contentType, type }, "Processing message");

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
					quotedMessageId, mediaUrl,
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

app.post("/send", auth, async (req, res) => {
	if (!isConnected) return res.status(503).json({ error: "Not connected" });
	const { jid, message, mediaUrl, contentType = "text", replyToMessageId, replyToText, replyToFromMe } = req.body;
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
