FROM node:24-bookworm-slim
WORKDIR /srv/chintamani
COPY inventory-service/package.json inventory-service/package-lock.json ./inventory-service/
RUN cd inventory-service && npm ci --omit=dev
COPY inventory-service/owner/ ./inventory-service/owner/
COPY inventory-service/lib/inventory-service.js ./inventory-service/lib/
WORKDIR /srv/chintamani/inventory-service
ENV NODE_ENV=production WORKSPACE_BIND=0.0.0.0 PORT=8080
USER node
EXPOSE 8080
CMD ["node", "owner/server.js"]
