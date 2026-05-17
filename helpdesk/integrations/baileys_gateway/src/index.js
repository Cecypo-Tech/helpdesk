const express = require("express");
const {
	makeWASocket, useMultiFileAuthState, DisconnectReason,
	makeCacheableSignalKeyStore, Browsers,
} = require("@whiskeysockets/baileys");
const pino = require("pino");
const { Boom } = require("@hapi/boom");
const axios = require("axios");
const qrcode = require("qrcode");

const API_KEY      = process.env.API_KEY      || "changeme";
const WEBHOOK_URL  = process.env.WEBHOOK_URL  || "";
const SESSION_NAME = process.env.SESSION_NAME || "helpdesk";
const PORT         = parseInt(process.env.PORT || "3000");
const SESSION_DIR  = `/app/sessions/${SESSION_NAME}`;

const logger = pino({ level: "info" });
let sock = null, qrString = null, isConnected = false;

async function connectToWhatsApp() {
	const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
	sock = makeWASocket({
		auth: {
			creds: state.creds,
			keys: makeCacheableSignalKeyStore(state.keys, pino({ level: "silent" })),
		},
		browser: Browsers.ubuntu("Chrome"),
		logger: pino({ level: "silent" }),
	});
	sock.ev.on("creds.update", saveCreds);
	sock.ev.on("connection.update", ({ connection, lastDisconnect, qr }) => {
		if (qr) { qrString = qr; isConnected = false; logger.info("QR code ready"); }
		logger.info({ connection, qr: !!qr }, "connection.update");
		if (connection === "close") {
			isConnected = false; qrString = null;
			const code = lastDisconnect?.error instanceof Boom ? lastDisconnect.error.output.statusCode : null;
			logger.warn({ code, reason: lastDisconnect?.error?.message }, "Connection closed");
			if (code !== DisconnectReason.loggedOut) setTimeout(connectToWhatsApp, 5000);
		} else if (connection === "open") { isConnected = true; qrString = null; logger.info("Connected"); }
	});
	sock.ev.on("messages.upsert", async ({ messages, type }) => {
		if (type !== "notify" || !WEBHOOK_URL) return;
		for (const msg of messages) {
			if (msg.key.fromMe || !msg.message) continue;
			const jid        = msg.key.remoteJid;
			const sender     = msg.key.participant || jid;
			const senderName = msg.pushName || sender.split("@")[0];
			const mc         = msg.message;
			let text = "", contentType = "text";
			if      (mc.conversation)          { text = mc.conversation; }
			else if (mc.extendedTextMessage)   { text = mc.extendedTextMessage.text; }
			else if (mc.imageMessage)          { contentType = "image";    text = mc.imageMessage.caption || ""; }
			else if (mc.videoMessage)          { contentType = "video";    text = mc.videoMessage.caption || ""; }
			else if (mc.audioMessage)          { contentType = "audio"; }
			else if (mc.documentMessage)       { contentType = "document"; text = mc.documentMessage.caption || ""; }
			else continue;
			try {
				await axios.post(WEBHOOK_URL, {
					jid, messageId: msg.key.id, sender, senderName,
					message: text, contentType, timestamp: msg.messageTimestamp,
				}, { headers: { "X-API-Key": API_KEY }, timeout: 15000 });
			} catch (err) { logger.error({ err: err.message, jid }, "Webhook failed"); }
		}
	});
}

const app = express();
app.use(express.json());
const auth = (req, res, next) =>
	req.headers["x-api-key"] === API_KEY ? next() : res.status(401).json({ error: "Unauthorized" });

app.get("/health", (_, res) => res.json({ connected: isConnected, hasQr: !!qrString, session: SESSION_NAME }));

app.get("/qr", async (_, res) => {
	if (isConnected) return res.json({ connected: true });
	if (!qrString)   return res.status(202).json({ waiting: true });
	const dataUrl = await qrcode.toDataURL(qrString);
	res.send(`<html><body style="background:#111;display:flex;align-items:center;justify-content:center;height:100vh">
		<img src="${dataUrl}" style="width:300px;height:300px"/></body></html>`);
});

app.post("/send", auth, async (req, res) => {
	if (!isConnected) return res.status(503).json({ error: "Not connected" });
	const { jid, message, mediaUrl, contentType = "text" } = req.body;
	if (!jid || (!message && !mediaUrl)) return res.status(400).json({ error: "jid and message/mediaUrl required" });
	try {
		let sent;
		if (!mediaUrl || contentType === "text") {
			sent = await sock.sendMessage(jid, { text: message });
		} else if (contentType === "image") {
			sent = await sock.sendMessage(jid, { image: { url: mediaUrl }, caption: message || "" });
		} else if (contentType === "video") {
			sent = await sock.sendMessage(jid, { video: { url: mediaUrl }, caption: message || "" });
		} else if (contentType === "audio") {
			sent = await sock.sendMessage(jid, { audio: { url: mediaUrl }, mimetype: "audio/mp4" });
		} else {
			sent = await sock.sendMessage(jid, { document: { url: mediaUrl }, fileName: message || "file" });
		}
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

app.listen(PORT, () => { logger.info(`Gateway :${PORT}`); connectToWhatsApp(); });
