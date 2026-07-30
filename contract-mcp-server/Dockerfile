# ---------- Build Stage ----------
FROM node:22-alpine AS builder

WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm ci

# Copy source
COPY . .

# Compile TypeScript
RUN npm run build

# ---------- Runtime Stage ----------
FROM node:22-alpine

WORKDIR /app

ENV NODE_ENV=production

# Install only production dependencies
COPY package*.json ./
RUN npm ci --omit=dev

# Copy compiled application
COPY --from=builder /app/dist ./dist

# Default MCP port
ENV PORT=3000

EXPOSE 3000

CMD ["npm", "start"]
