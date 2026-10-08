# The React app, built once and served by nginx, which also forwards /api (and
# the Django admin) to the backend container. The app only ever calls relative
# /api paths, so the browser sees one origin and no CORS is involved.

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# The official image renders /etc/nginx/templates/*.template through envsubst at
# start, so BACKEND_URL can point somewhere other than the compose service.
ENV BACKEND_URL=http://backend:8000
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
