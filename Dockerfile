FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
ENV VINTED_MCP_TRANSPORT=http \
    PORT=8000 \
    VINTED_WISHLIST_PATH=/data/wishlist.json
VOLUME ["/data"]
EXPOSE 8000
CMD ["vinted-mcp"]
