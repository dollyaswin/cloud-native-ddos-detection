import time
import os
import subprocess
import threading
import onnxruntime as ort
import numpy as np
import logging
import sys
import psutil
import docker

logging.basicConfig(level=logging.INFO, format='%(asctime)s - NIDS - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger("RealTime-NIDS")

EXPECTED_FEATURES = [
    "Flow Duration", "Total Fwd Packets", "Total Length of Fwd Packets", 
    "Fwd Packet Length Max", "Bwd Packet Length Max", "Bwd Packet Length Min", 
    "Flow IAT Mean", "Flow IAT Min", "Bwd IAT Total", "Bwd IAT Mean", 
    "Fwd PSH Flags", "Fwd Packets/s", "Bwd Packets/s", "Max Packet Length", 
    "ACK Flag Count", "URG Flag Count", "Down/Up Ratio", 
    "Init_Win_bytes_forward", "Init_Win_bytes_backward", "min_seg_size_forward", 
    "Active Mean", "Active Std", "Idle Std"
]

def normalize_string(s):
    return s.lower().replace(" ", "").replace("_", "").replace("/", "")

NORMALIZED_EXPECTED = [normalize_string(f) for f in EXPECTED_FEATURES]

class RealTimeNIDS:
    def __init__(self):
        self.model_path = "model.onnx"
        self.csv_path = "live_flows.csv"
        # Gunakan 'any' untuk menangkap semua interface atau 'eth0' secara spesifik
        self.interface = "eth0"
        self.session = None
        self.input_name = None
        
        try:
            self.session = ort.InferenceSession(self.model_path)
            self.input_name = self.session.get_inputs()[0].name
            logger.info("ONNX Model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load ONNX model: {e}")
            sys.exit(1)
            
        if os.path.exists(self.csv_path):
            os.remove(self.csv_path)
            
        # Variabel untuk pencatatan metrik paper
        self.latencies = []
        self.total_flows = 0
        
        self.docker_client = None
        try:
            self.docker_client = docker.from_env()
            logger.info("Connected to Docker Engine API for metrics.")
        except Exception as e:
            logger.warning(f"Could not connect to Docker Engine: {e}")

    def start_sniffer(self):
        logger.info(f"Starting cicflowmeter on interface {self.interface}...")
        
        # Perintah ini membutuhkan priviliges NET_RAW
        cmd = ["cicflowmeter", "-i", self.interface, "-c", self.csv_path]
        self.sniffer_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Cek apakah cicflowmeter mati seketika
        time.sleep(2)
        if self.sniffer_process.poll() is not None:
            stderr = self.sniffer_process.stderr.read().decode('utf-8')
            logger.error(f"Failed to start cicflowmeter: {stderr}")
            sys.exit(1)
            
        self.tailer_thread = threading.Thread(target=self.tail_csv)
        self.tailer_thread.daemon = True
        self.tailer_thread.start()
        
    def tail_csv(self):
        logger.info("Waiting for cicflowmeter to create output file...")
        while not os.path.exists(self.csv_path):
            time.sleep(1)
            
        logger.info(f"File {self.csv_path} created. Tailing for new flows...")
        
        with open(self.csv_path, "r") as f:
            header_line = f.readline()
            while not header_line:
                time.sleep(0.5)
                f.seek(0, 1)
                header_line = f.readline()
                
            headers = [h.strip() for h in header_line.split(",")]
            normalized_headers = [normalize_string(h) for h in headers]
            
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.5)
                    f.seek(0, 1)
                    continue
                
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < len(headers):
                    continue
                
                self.process_flow(parts, headers, normalized_headers)
                
    def process_flow(self, parts, headers, normalized_headers):
        # Cari index src_ip dan dst_ip dari cicflowmeter
        src_ip_idx = normalized_headers.index("srcip") if "srcip" in normalized_headers else 1
        dst_ip_idx = normalized_headers.index("dstip") if "dstip" in normalized_headers else 3
        
        src_ip = parts[src_ip_idx] if src_ip_idx < len(parts) else "Unknown"
        dst_ip = parts[dst_ip_idx] if dst_ip_idx < len(parts) else "Unknown"
        
        # MODEL ONNX MEMBUTUHKAN TEPAT 77 FITUR (bukan 23)!
        # cicflowmeter menghasilkan 84 kolom. 7 kolom pertama adalah identifier string.
        # Kolom 0-6: Flow ID, Src IP, Src Port, Dst IP, Dst Port, Protocol, Timestamp
        # Sisa kolom (index 7 sampai 83) adalah tepat 77 fitur numerik yang dipakai model.
        features = []
        for i in range(7, min(84, len(parts))):
            try:
                features.append(float(parts[i]))
            except ValueError:
                features.append(0.0)
                
        # Padding dengan 0 jika kurang dari 77 (misal karena versi cicflowmeter berbeda)
        while len(features) < 77:
            features.append(0.0)
            
        # Potong jika lebih dari 77
        features = features[:77]
                
        input_data = np.array([features], dtype=np.float32)
        
        start_time = time.time()
        ort_outs = self.session.run(None, {self.input_name: input_data})
        latency = time.time() - start_time
        
        # Rekam latensi
        self.latencies.append(latency)
        self.total_flows += 1
        
        prediction = ort_outs[0][0]
        is_malicious = False
        if isinstance(prediction, (np.ndarray, list)):
            if len(prediction) > 1 and prediction[1] > 0.5:
                is_malicious = True
        else:
            if prediction > 0.5:
                is_malicious = True
                
        if is_malicious:
            logger.warning(f"[DDoS ALERT] Flow dari {src_ip} ke {dst_ip} terdeteksi sebagai ANOMALI/DDoS! (Latensi: {latency*1000:.2f}ms)")
        else:
            logger.info(f"[AMAN] Flow dari {src_ip} ke {dst_ip} terpantau normal. (Latensi: {latency*1000:.2f}ms)")
            
    def start_metric_reporter(self):
        self.reporter_thread = threading.Thread(target=self.report_metrics_loop)
        self.reporter_thread.daemon = True
        self.reporter_thread.start()
        
    def report_metrics_loop(self):
        while True:
            time.sleep(10) # Cetak laporan setiap 10 detik
            
            container_metrics = {}
            if self.docker_client:
                try:
                    for c in self.docker_client.containers.list():
                        # Filter container aplikasi kita
                        if "fastapi" in c.name or "envoy" in c.name or "ml-ext-proc" in c.name:
                            try:
                                stats = c.stats(stream=False)
                                
                                cpu_percent = 0.0
                                cpu_delta = float(stats["cpu_stats"]["cpu_usage"]["total_usage"]) - float(stats.get("precpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0))
                                system_delta = float(stats["cpu_stats"].get("system_cpu_usage", 0)) - float(stats.get("precpu_stats", {}).get("system_cpu_usage", 0))
                                if system_delta > 0.0:
                                    cpu_count = len(stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1]))
                                    cpu_percent = (cpu_delta / system_delta) * cpu_count * 100.0
                                    
                                mem_usage = stats["memory_stats"].get("usage", 0)
                                mem_cache = stats["memory_stats"].get("stats", {}).get("inactive_file", 0)
                                if mem_cache == 0:
                                    mem_cache = stats["memory_stats"].get("stats", {}).get("cache", 0)
                                mem_mb = (mem_usage - mem_cache) / (1024 * 1024)
                                
                                container_metrics[c.name] = {
                                    "cpu_percent": round(cpu_percent, 2),
                                    "memory_mb": round(mem_mb, 2)
                                }
                            except Exception as e:
                                container_metrics[c.name] = f"Error: {e}"
                except Exception as e:
                    logger.warning(f"Error fetching docker stats: {e}")
            
            if self.total_flows > 0:
                p50 = np.percentile(self.latencies, 50) * 1000
                p95 = np.percentile(self.latencies, 95) * 1000
                p99 = np.percentile(self.latencies, 99) * 1000
                avg = np.mean(self.latencies) * 1000
                
                # Menggunakan JSON format agar mudah di-parse/disimpan ke file untuk keperluan paper
                import json
                report = {
                    "metric_type": "nids_performance",
                    "total_flows_processed": self.total_flows,
                    "latency_ms": {
                        "avg": round(avg, 4),
                        "p50": round(p50, 4),
                        "p95": round(p95, 4),
                        "p99": round(p99, 4)
                    },
                    "container_resources": container_metrics
                }
                logger.info(f"EXPERIMENT_METRICS | {json.dumps(report)}")

if __name__ == "__main__":
    nids = RealTimeNIDS()
    nids.start_sniffer()
    nids.start_metric_reporter()
    logger.info("Real-Time NIDS berjalan. Menunggu traffic jaringan...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping NIDS...")
        nids.sniffer_process.terminate()
