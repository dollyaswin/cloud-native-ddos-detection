# Real-time Distributed Denial-of-Service Detection on Cloud-Deployed API Gateway

## Architecture Flow

This application uses Envoy Proxy as the API Gateway, supported by a Machine Learning External Processing (ext-proc) filter for real-time anomaly and DDoS detection. Safe traffic is then forwarded to the FastAPI backend.

```text
       +---------------------------------------------------+
       |                  Kubernetes Pod                   |
       |  (Simulated via docker-compose network_mode)      |
       |                                                   |
[User] |    +-------+ (gRPC / Port 50051) +-------------+  |
  |    |    |       |<===================>|             |  |
  +=======> | Envoy |                       | ML Ext-Proc |  |
(Port 8080) | Proxy |<------------------- | (ONNX)      |  |
       |    |       |     (Safe Traffic)  +-------------+  |
       |    +-------+                                      |
       |        | (HTTP / Port 8000)                       |
       |        v                                          |
       |  +-----------+                                    |
       |  |  FastAPI  |                                    |
       |  +-----------+                                    |
       +---------------------------------------------------+
```

### Port Configuration in Docker Compose
In a Kubernetes environment, these three containers run in a single Pod and share the same network namespace (`localhost`). 

To simulate this locally, Docker Compose configures Envoy and the ML filter to use `network_mode: "service:fastapi"`. Because they share the FastAPI container's network interface, the host port mapping (exposing Envoy's port `8080`) must be defined under the `fastapi` service in `docker-compose.yaml`.

The internal ports 8000 (FastAPI) and 50051 (ML) are not exposed to the host. This ensures that external requests cannot bypass Envoy to reach the backend directly.

### Protocol Buffers and gRPC
- **Protocol Buffers (Protobuf)**: Used as the data contract between Envoy Proxy and the ML Ext-Proc service. The `xds-protos` Python package provides pre-compiled Envoy protobuf definitions.
- **gRPC**: A high-performance protocol used for communication between Envoy and the ML service. It supports bi-directional streaming, allowing Envoy to stream HTTP headers and bodies to the ML filter asynchronously with minimal latency.

### Machine Learning Model
By default, the application uses a pre-trained ONNX model (`model.onnx`) for DDoS detection. This model is based on XGBoost and can be found on Hugging Face:
- **Model Card**: [DDoS Detection using XGBoost (ONNX)](https://huggingface.co/darkknight25/ddos_xgboost_onnx)

---

## 1. Build the Application
To build the container images for FastAPI and the ML service, run the following command in the project root directory:
```bash
docker-compose build
```

## 2. Run the Application
Start all services in detached mode:
```bash
docker-compose up -d
```
To monitor the application logs in real-time:
```bash
docker-compose logs -f
```

## 3. Test the Application
The application is accessible via Envoy Proxy on port 8080. Open a new terminal and run the verbose `curl` commands to see how the traffic is routed. Notice the `server: envoy` response header, which confirms the traffic is being handled by the proxy before reaching FastAPI.

### 1. Test Root Endpoint
```bash
curl -v http://localhost:8080/
```
**Expected Output:**
```text
* Host localhost:8080 was resolved.
* IPv6: ::1
* IPv4: 127.0.0.1
*   Trying [::1]:8080...
* Connected to localhost (::1) port 8080
> GET / HTTP/1.1
> Host: localhost:8080
> User-Agent: curl/8.7.1
> Accept: */*
>
* Request completely sent off
< HTTP/1.1 200 OK
< date: Thu, 11 Jun 2026 06:36:37 GMT
< server: envoy
< content-length: 111
< content-type: application/json
< x-envoy-upstream-service-time: 9
<
* Connection #0 to host localhost left intact
{"status":"success","message":"Hello World from FastAPI!","architecture":"Traffic routed via Envoy & Ext-Proc"}
```

### 2. Test Healthcheck Endpoint
```bash
curl -v http://localhost:8080/health
```
**Expected Output:**
```text
* Host localhost:8080 was resolved.
* IPv6: ::1
* IPv4: 127.0.0.1
*   Trying [::1]:8080...
* Connected to localhost (::1) port 8080
> GET /health HTTP/1.1
> Host: localhost:8080
> User-Agent: curl/8.7.1
> Accept: */*
>
* Request completely sent off
< HTTP/1.1 200 OK
< date: Thu, 11 Jun 2026 06:37:02 GMT
< server: envoy
< content-length: 20
< content-type: application/json
< x-envoy-upstream-service-time: 12
<
* Connection #0 to host localhost left intact
{"status":"healthy"}
```

## 4. Experiment & Metric Extraction (For Scientific Papers)

For research and benchmarking purposes, this application is configured to output high-precision inference latency metrics in structured JSON format via application logs. Meanwhile, container-level resource metrics (CPU/Memory) must be collected externally.

### 4.1. Collecting CPU and Memory Metrics
To ensure the Python application does not suffer from measurement overhead, track the resource usage of the container externally using `docker stats`.

Run the following bash script in a new terminal *while your load test is running*:

```bash
# Record CPU and RAM usage of the ml-ext-proc container every 1 second into a CSV file
echo "timestamp,container,cpu_percent,mem_usage" > docker_metrics.csv
while true; do
  timestamp=$(date +%s)
  stats=$(docker stats --no-stream --format "{{.Name}},{{.CPUPerc}},{{.MemUsage}}" | grep ml-ext-proc)
  echo "$timestamp,$stats" >> docker_metrics.csv
  sleep 1
done
```

### 4.2. Extracting Inference Latency
The `ml-ext-proc` application prints a JSON log for every request it processes. Once your experiment finishes, you can extract these logs into a `.txt` file and convert them into a CSV for data analysis (e.g., using Python/Pandas, R, or Excel).

1. **Export the logs to a file:**
```bash
docker-compose logs ml-ext-proc > experiment_logs.txt
```

2. **Convert the logs to CSV:**
You can use the following Python script to parse the `experiment_logs.txt` file and generate a `latency_results.csv`:

```python
import json
import csv

with open('experiment_logs.txt', 'r') as log_file, open('latency_results.csv', 'w', newline='') as csv_file:
    writer = csv.writer(csv_file)
    writer.writerow(['timestamp', 'metric_type', 'latency_seconds']) # CSV Header
    
    for line in log_file:
        if 'EXPERIMENT_DATA |' in line:
            json_str = line.split('EXPERIMENT_DATA | ')[1].strip()
            data = json.loads(json_str)
            writer.writerow([data['timestamp'], data['metric_type'], data['value']])

print("Data successfully extracted to latency_results.csv!")
```
This structured logging approach is highly recommended for scientific paper publications, ensuring reproducible and easily parsable benchmark datasets.
