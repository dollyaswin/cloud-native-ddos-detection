FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
# Membutuhkan grpcio, protobuf, onnxruntime
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY model.onnx .

# Expose port 50051 untuk gRPC server
EXPOSE 50051

CMD ["python", "server.py"]
