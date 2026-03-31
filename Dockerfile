FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY agents/ agents/
COPY services/ services/
COPY templates/ templates/
COPY app.py tools.py state.py ./

# Copy example slides for RAG indexing (use JSON array form for paths with spaces)
COPY ["example slides/", "example slides/"]

# ChromaDB will persist here
RUN mkdir -p .chroma_db

# Pre-index RAG at build time so cold starts are fast
RUN SKIP_RAG_INDEX= python -c "from services.rag import index_example_slides; index_example_slides()"

# Cloud Run sets PORT env var
ENV PORT=8080

EXPOSE 8080

# Use gunicorn for production
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 300 app:app
