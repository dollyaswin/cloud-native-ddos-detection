# Real-time Out-of-Band DDoS Detection on Cloud-Deployed API Gateway

This application implements a highly scalable, out-of-band Network Intrusion Detection System (NIDS) designed to protect a FastAPI backend behind an Envoy API Gateway. Instead of intercepting traffic inline (which can cause severe latency bottlenecks during a DDoS attack), this architecture uses a Micro-Batching approach with a Machine Learning Stacking Ensemble to analyze traffic passively in real-time.

## Architecture Flow

The system captures raw network packets directly from the Docker container's network interface using `tcpdump`. Traffic is sliced into 5-second PCAP files and processed by CICFlowMeter to extract 23 statistical features. These features are then evaluated by a sophisticated Stacking Meta-Model.

```text
       +-------------------------------------------------------------+
       |                     Kubernetes Pod                          |
       |       (Simulated via docker compose network_mode)           |
       |                                                             |
[User] |    +-------+ (HTTP / Port 8000) +-------------+             |
  |    |    |       |===================>|             |             |
  +=======> | Envoy |                    |   FastAPI   |             |
(Port 8080) | Proxy |<===================|             |             |
       |    +-------+                    +-------------+             |
       |        |                               |                    |
       |        |       [Out-of-Band Sniffing]  |                    |
       |        +...............................+                    |
       |                        |                                    |
       |                        v                                    |
       |              +-------------------+                          |
       |              |   ML NIDS Sniffer |                          |
       |              |   (tcpdump -G 5)  |                          |
       |              +-------------------+                          |
       |                        | PCAP                               |
       |                        v                                    |
       |                 CICFlowMeter (CSV)                          |
       |                        | 23 Features                        |
       |                        v                                    |
       |             [ ML Stacking Ensemble ]                        |
       |            (XGBoost + LightGBM + CatBoost)                  |
       |                        | 3 Probabilities                    |
       |                        v                                    |
       |                Meta-Model (ONNX)                            |
       |                        |                                    |
       |                        v                                    |
       |               [DDoS ALERT] / [AMAN]                         |
       +-------------------------------------------------------------+
```

### Out-of-Band Micro-Batching
Instead of evaluating packets one by one online (which is prone to CPU hanging and buffer overflows), the NIDS uses `tcpdump` to record traffic and forcefully flushes the flows every 5 seconds. This guarantees that incomplete flows (such as those in a SYN Flood) are extracted immediately and accurately without waiting for standard TCP timeouts.

### Machine Learning Stacking Pipeline
The NIDS uses a two-stage Stacking Ensemble to achieve maximum accuracy:
1. **Base Models**: XGBoost, LightGBM, and CatBoost evaluate the 23 statistical features extracted from the network flow and output a probability score (0 to 1).
2. **Meta-Model**: An ONNX Meta-Model takes the 3 probabilities from the base models and makes the final classification (Normal vs. DDoS).

---

## 1. Prerequisites

Before running the application, you must place the 4 required ONNX models into the `models/` directory:

```bash
apps/ml-ext-proc/models/
├── model.onnx  # Meta-Model (Takes 3 probabilities)
├── xgb.onnx    # XGBoost Base Model (Takes 23 features)
├── lgb.onnx    # LightGBM Base Model (Takes 23 features)
└── cat.onnx    # CatBoost Base Model (Takes 23 features)
```

## 2. Run the Application

Build and start all services in detached mode using Docker Compose:
```bash
docker compose up --build -d
```

Monitor the application logs in real-time to see the NIDS in action:
```bash
docker compose logs -f ml-ext-proc
```

## 3. Test the Detection

To verify the Stacking model's detection capabilities and measure the latency impact, two test scripts are provided in the repository: `run_client.sh` (simulating normal HTTP traffic) and `run_attack.sh` (simulating a DDoS SYN Flood).

1. Ensure the NIDS is running and waiting for traffic:
   ```text
   [TCPDUMP STARTED] tcpdump: listening on eth0, link-type EN10MB...
   Micro-Batch Processor started. Menunggu traffic...
   ```
2. Start the normal traffic load test on your client VM:
   ```bash
   ./run_client.sh
   ```
3. Simultaneously, launch the DDoS attack on your attacker VM:
   ```bash
   sudo ./run_attack.sh
   ```
4. Check the `ml-ext-proc` logs. Every 5 seconds, the system will output the evaluation results along with the inference latencies and probabilities:
   ```text
   [DDoS ALERT] Flow dari 10.148.0.2 ke 10.148.0.5 terdeteksi sebagai ANOMALI/DDoS! (Prob: XGB=0.99 LGB=0.98 CAT=0.99) (Latensi: 1.15ms)
   ```

## 4. Experiment & Metric Extraction (For Scientific Papers)

For research and benchmarking purposes, this application automatically measures high-precision inference latencies (Avg, P50, P95, P99) and container-level resource utilization (CPU & Memory for Envoy, FastAPI, and ML). 

These metrics are aggregated and printed every 10 seconds in a structured JSON format to standard output.

### Extracting the Metrics
Once your experiment finishes, you can filter the logs to extract only the JSON metric reports:

```bash
docker compose logs ml-ext-proc | grep "EXPERIMENT_METRICS" > metrics.log
```

**Sample Log Output:**
```json
{
  "metric_type": "nids_performance",
  "total_flows_processed": 1450,
  "latency_ms": {
    "avg": 1.2,
    "p50": 1.1,
    "p95": 2.4,
    "p99": 3.1
  },
  "container_resources": {
    "app-ml-ext-proc-1": {"cpu_percent": 25.6, "memory_mb": 166.0},
    "app-envoy-1": {"cpu_percent": 10.3, "memory_mb": 29.3},
    "app-fastapi-1": {"cpu_percent": 15.2, "memory_mb": 38.1}
  }
}
```
This structured logging approach is highly recommended for scientific paper publications, ensuring reproducible and easily parsable benchmark datasets.
