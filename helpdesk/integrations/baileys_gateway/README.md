# Baileys WhatsApp Gateway — Helpdesk

A lightweight Node.js gateway that bridges WhatsApp (groups + individual DMs) to the Helpdesk via a simple REST API and webhook.

## Directory layout (on the host)

```
/opt/whatsapp-gateway-helpdesk/
├── Dockerfile
├── package.json
├── src/
│   └── index.js
└── sessions/            ← created automatically, contains Baileys auth state
```

## Files

### `package.json`

```json
{
  "name": "whatsapp-gateway-helpdesk",
  "version": "1.0.0",
  "main": "src/index.js",
  "scripts": { "start": "node src/index.js" },
  "dependencies": {
    "@hapi/boom": "^10.0.1",
    "@whiskeysockets/baileys": "^6.7.16",
    "axios": "^1.7.0",
    "express": "^4.19.0",
    "pino": "^8.21.0",
    "qrcode": "^1.5.3"
  }
}
```

### `Dockerfile`

```dockerfile
FROM node:20-alpine
RUN apk add --no-cache python3 make g++
WORKDIR /app
COPY package*.json ./
RUN npm install --omit=dev
COPY src/ ./src/
VOLUME ["/app/sessions"]
EXPOSE 3000
CMD ["node", "src/index.js"]
```

### `src/index.js`

```javascript
const express = require("express");
const {
  makeWASocket, useMultiFileAuthState, DisconnectReason,
  makeCacheableSignalKeyStore,
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
    printQRInTerminal: true,
    logger: pino({ level: "silent" }),
  });
  sock.ev.on("creds.update", saveCreds);
  sock.ev.on("connection.update", ({ connection, lastDisconnect, qr }) => {
    if (qr) { qrString = qr; isConnected = false; logger.info("QR code ready"); }
    if (connection === "close") {
      isConnected = false; qrString = null;
      const code = lastDisconnect?.error instanceof Boom ? lastDisconnect.error.output.statusCode : null;
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
```

---

## Portainer Stack

Paste into **Portainer → Stacks → Add Stack → Web editor**:

```yaml
version: "3.8"

services:
  whatsapp-gateway-helpdesk:
    build:
      context: /opt/whatsapp-gateway-helpdesk
    image: whatsapp-gateway-helpdesk:latest
    container_name: whatsapp-gateway-helpdesk
    restart: unless-stopped
    ports:
      - "3001:3000"
    environment:
      API_KEY: "REPLACE_WITH_STRONG_RANDOM_KEY"
      WEBHOOK_URL: "http://<frappe-host>:8002/api/method/helpdesk.integrations.baileys.webhook"
      SESSION_NAME: "helpdesk"
      PORT: "3000"
    volumes:
      - /opt/whatsapp-gateway-helpdesk/sessions:/app/sessions
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:3000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

> **Tip — `WEBHOOK_URL`:** If Frappe runs on the same host as the container, use the host's LAN IP (e.g. `http://192.168.1.10:8002/...`) or `http://host.docker.internal:8002/...`. Do **not** use `localhost` — that resolves to the container itself.

---

## First-run

**1. Build the image** (once, on the server):
```bash
cd /opt/whatsapp-gateway-helpdesk
docker build -t whatsapp-gateway-helpdesk:latest .
```

**2. Deploy the stack** in Portainer. Fill in `API_KEY` and `WEBHOOK_URL` before saving.

**3. Scan the QR code** — open in a browser:
```
http://<server-ip>:3001/qr
```
Scan with the WhatsApp number you want to use. Do **not** use a number already registered on WhatsApp Business API — use a standard WhatsApp account.

**4. Verify connection:**
```bash
curl http://<server-ip>:3001/health
# {"connected":true,"hasQr":false,"session":"helpdesk"}
```

---

## Helpdesk configuration

1. In Frappe Desk → **Baileys Gateway Settings**
2. Set **Gateway URL** → `http://<server-ip>:3001`
3. Set **API Key** → same value as `API_KEY` in the stack
4. Set **Session Name** → `helpdesk`
5. Set **Enabled** → checked
6. Configure ticket defaults, team routing, and (optionally) add group JIDs in the **Group Mappings** table.
7. Run `bench --site site16.local migrate` to create the new DocTypes.

---

## API contract

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /health` | none | Connection status |
| `GET /qr` | none | HTML page with QR code for scanning |
| `POST /send` | X-API-Key | Send a message or media to a JID |
| `POST /markRead` | X-API-Key | Mark messages as read |
| webhook `POST` to Frappe | X-API-Key | Gateway delivers incoming messages here |

### `/send` payload
```json
{
  "sessionName": "helpdesk",
  "jid": "254712345678@s.whatsapp.net",
  "message": "Hello",
  "contentType": "text",
  "mediaUrl": null
}
```
Response: `{ "messageId": "ABCDEF..." }`

### Webhook payload (gateway → Frappe)
```json
{
  "jid": "120363XXXXXXXXXX@g.us",
  "messageId": "ABCDEF...",
  "sender": "254712345678@s.whatsapp.net",
  "senderName": "Alice",
  "message": "Hello from the group",
  "contentType": "text",
  "timestamp": 1716000000
}
```

---

## Session persistence

Auth state is stored in `/opt/whatsapp-gateway-helpdesk/sessions/helpdesk/`.  
**Back this directory up.** Losing it means the number gets logged out and needs a re-scan.

A container restart (or server reboot) does **not** require a re-scan as long as the volume is intact.

---

## Generating a strong API key

```bash
openssl rand -hex 32
```

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| QR page says "QR not ready yet" | Container still starting; wait 10s and refresh |
| `/health` shows `connected: false` after scanning | Check container logs: `docker logs whatsapp-gateway-helpdesk` |
| Webhook delivers but no ticket created | Check Frappe error log; confirm `Baileys Gateway Settings.enabled = 1` |
| Gateway can't reach Frappe webhook | Use LAN IP in `WEBHOOK_URL`, not `localhost` |
| Number gets banned | Use a dedicated number; avoid mass messaging |
