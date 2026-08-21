# Use lightweight Python 3.10 image
FROM python:3.10-slim

# Set the working directory
WORKDIR /app

# Install dependencies. requirements-gemini.txt is split out of
# requirements.txt so the ML prediction image (Dockerfile.predict) doesn't
# have to resolve google-generativeai against tensorflow's incompatible
# protobuf requirement -- see requirements-gemini.txt. The web service
# needs both.
COPY requirements.txt requirements-gemini.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-gemini.txt

# Copy the source code
COPY . .

# Expose port (Cloud Run defaults to 8080)
ENV PORT 8080
ENV USE_LOCAL_DATA False
EXPOSE $PORT

# Run the FastAPI application using Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips='*'"]
