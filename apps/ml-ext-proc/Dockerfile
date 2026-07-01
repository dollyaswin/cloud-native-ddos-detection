FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

# Install libpcap (dibutuhkan oleh cicflowmeter/scapy) dan iptables
RUN apt-get update && apt-get install -y libpcap-dev tcpdump iproute2 iptables && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Membutuhkan grpcio, protobuf, onnxruntime, cicflowmeter
RUN pip install --no-cache-dir -r requirements.txt

COPY realtime_sniffer.py .


CMD ["python", "realtime_sniffer.py"]
