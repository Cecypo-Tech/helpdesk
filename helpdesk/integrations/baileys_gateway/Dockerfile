FROM node:20-slim
RUN apt-get update && apt-get install -y python3 make g++ git ca-certificates --no-install-recommends && rm -rf /var/lib/apt/lists/*
RUN git config --global url."https://github.com/".insteadOf "git@github.com:" && \
    git config --global url."https://".insteadOf "ssh://git@"
WORKDIR /app
COPY package*.json ./
RUN npm install --omit=dev
COPY src/ ./src/
VOLUME ["/app/sessions"]
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "require('http').get('http://localhost:3000/health',(r)=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"
CMD ["node", "src/index.js"]
