FROM node:22-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# proxy /v1 to the api service inside the compose network
RUN printf 'server {\n  listen 80;\n  root /usr/share/nginx/html;\n  location /v1 { proxy_pass http://api:8000; }\n  location /healthz { proxy_pass http://api:8000; }\n  location / { try_files $uri /index.html; }\n}\n' > /etc/nginx/conf.d/default.conf
