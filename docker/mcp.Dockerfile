FROM node:20-alpine

RUN npm install -g @benborla29/mcp-server-mysql

CMD ["mcp-server-mysql"]
