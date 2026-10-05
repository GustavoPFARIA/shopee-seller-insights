FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build && chown -R node:node /app
USER node
EXPOSE 5173
CMD ["npm", "run", "preview"]
