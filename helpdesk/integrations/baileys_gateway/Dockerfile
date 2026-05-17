FROM node:20-slim
RUN apt-get update && apt-get install -y python3 make g++ git --no-install-recommends && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package*.json ./
RUN npm install --omit=dev
COPY src/ ./src/
VOLUME ["/app/sessions"]
EXPOSE 3000
CMD ["node", "src/index.js"]
