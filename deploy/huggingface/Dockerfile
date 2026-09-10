FROM node:24.20.0-bookworm-slim@sha256:6642ef280aebc09c4541bee0b15c9f89f0f3f3c247ddee79ae1d37eddfdcbbaa AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 SIDEFX_HF_SPACE=1 SIDEFX_LAB_ENABLED=1
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npx next build

FROM node:24.20.0-bookworm-slim@sha256:6642ef280aebc09c4541bee0b15c9f89f0f3f3c247ddee79ae1d37eddfdcbbaa
WORKDIR /app
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=7860 SIDEFX_HF_SPACE=1 SIDEFX_LAB_ENABLED=1 NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/generated ./generated
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 7860
CMD ["node", "server.js"]
