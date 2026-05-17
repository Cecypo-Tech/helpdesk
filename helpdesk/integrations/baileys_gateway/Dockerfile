FROM node:20-alpine
RUN apk add --no-cache python3 make g++
WORKDIR /app
COPY package*.json ./
RUN npm install --omit=dev
COPY src/ ./src/
VOLUME ["/app/sessions"]
EXPOSE 3000
CMD ["node", "src/index.js"]
