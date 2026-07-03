# Stage 1: Build React Static Assets
FROM node:18-slim AS builder
WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build

# Stage 2: Serve với Nginx siêu nhẹ tiêu chuẩn sản xuất
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
# Copy file config để chuyển hướng API
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]