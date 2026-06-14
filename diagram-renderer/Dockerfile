FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PUPPETEER_SKIP_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

COPY package*.json ./
RUN npm install --omit=dev
COPY server.mjs ./

EXPOSE 3010
CMD ["npm", "start"]
