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
import glob
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - NIDS - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger("RealTime-NIDS")

class MicroBatchNIDS:
    def __init__(self):
        self.interface = "eth0"
        self.pcap_dir = "/app/pcap_data"
        os.makedirs(self.pcap_dir, exist_ok=True)
        
        self.meta_model_path = "/app/models/model.onnx"
        self.xgb_path = "/app/models/xgb.onnx"
        self.lgb_path = "/app/models/lgb.onnx"
        self.cat_path = "/app/models/cat.onnx"
        
        self.session_meta = None
        self.session_xgb = None
        self.session_lgb = None
        self.session_cat = None
        
        try:
            self.session_meta = ort.InferenceSession(self.meta_model_path)
            self.meta_input = self.session_meta.get_inputs()[0].name
            logger.info("Meta ONNX Model loaded successfully.")
            
            # Load base models if they exist and are not empty
            if os.path.exists(self.xgb_path) and os.path.getsize(self.xgb_path) > 0:
                self.session_xgb = ort.InferenceSession(self.xgb_path)
                self.xgb_input = self.session_xgb.get_inputs()[0].name
                logger.info("XGBoost Model loaded.")
            else:
                logger.warning(f"Model {self.xgb_path} belum valid/kosong. Siapkan file ini agar Stacking bisa berjalan maksimal.")
                
            if os.path.exists(self.lgb_path) and os.path.getsize(self.lgb_path) > 0:
                self.session_lgb = ort.InferenceSession(self.lgb_path)
                self.lgb_input = self.session_lgb.get_inputs()[0].name
                logger.info("LightGBM Model loaded.")
            else:
                logger.warning(f"Model {self.lgb_path} belum valid/kosong. Siapkan file ini agar Stacking bisa berjalan maksimal.")
                
            if os.path.exists(self.cat_path) and os.path.getsize(self.cat_path) > 0:
                self.session_cat = ort.InferenceSession(self.cat_path)
                self.cat_input = self.session_cat.get_inputs()[0].name
                logger.info("CatBoost Model loaded.")
            else:
                logger.warning(f"Model {self.cat_path} belum valid/kosong. Siapkan file ini agar Stacking bisa berjalan maksimal.")
                
        except Exception as e:
            logger.error(f"Failed to load ONNX models: {e}")
            sys.exit(1)
            
        self.latencies = []
        self.total_flows = 0
        
        self.docker_client = None
        try:
            self.docker_client = docker.from_env()
            logger.info("Connected to Docker Engine API for metrics.")
        except Exception:
            pass
            
        self.pcap_dir = "/app/pcap_data"
        os.makedirs(self.pcap_dir, exist_ok=True)
        
        # Bersihkan pcap lama
        for f in glob.glob(os.path.join(self.pcap_dir, "*")):
            os.remove(f)

    def start_metric_reporter(self):
        t = threading.Thread(target=self.report_metrics_loop, daemon=True)
        t.start()
        
    def report_metrics_loop(self):
        while True:
            time.sleep(10)
            
            container_metrics = {}
            if self.docker_client:
                try:
                    for c in self.docker_client.containers.list():
                        if "fastapi" in c.name or "envoy" in c.name or "ml-ext-proc" in c.name:
                            try:
                                stats = c.stats(stream=False)
                                cpu_delta = float(stats["cpu_stats"]["cpu_usage"]["total_usage"]) - float(stats.get("precpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0))
                                system_delta = float(stats["cpu_stats"].get("system_cpu_usage", 0)) - float(stats.get("precpu_stats", {}).get("system_cpu_usage", 0))
                                cpu_percent = 0.0
                                if system_delta > 0.0:
                                    cpu_percent = round((cpu_delta / system_delta) * len(stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1])) * 100.0, 2)
                                mem_usage = stats["memory_stats"].get("usage", 0) - stats["memory_stats"].get("stats", {}).get("cache", 0)
                                container_metrics[c.name] = {"cpu_percent": cpu_percent, "memory_mb": round(mem_usage / 1024 / 1024, 2)}
                            except Exception:
                                pass
                except Exception:
                    pass
            
            p50 = p95 = p99 = avg = 0.0
            if self.total_flows > 0 and len(self.latencies) > 0:
                p50 = np.percentile(self.latencies, 50) * 1000
                p95 = np.percentile(self.latencies, 95) * 1000
                p99 = np.percentile(self.latencies, 99) * 1000
                avg = np.mean(self.latencies) * 1000
                
            import json
            report = {
                "metric_type": "nids_performance",
                "total_flows_processed": self.total_flows,
                "latency_ms": {"avg": round(avg, 4), "p50": round(p50, 4), "p95": round(p95, 4), "p99": round(p99, 4)},
                "container_resources": container_metrics
            }
            logger.info(f"EXPERIMENT_METRICS | {json.dumps(report)}")

    def extract_and_infer(self, pcap_file):
        csv_file = pcap_file.replace(".pcap", ".csv")
        
        # Ekstrak 84 fitur menggunakan metode MANUAL agar bisa FORCE FLUSH di akhir pcap
        try:
            from scapy.all import PcapReader
            from cicflowmeter.flow_session import FlowSession
            
            setattr(FlowSession, "output_mode", "csv")
            setattr(FlowSession, "output", csv_file)
            setattr(FlowSession, "fields", None)
            setattr(FlowSession, "verbose", False)
            
            session = FlowSession()
            with PcapReader(pcap_file) as pcap:
                for pkt in pcap:
                    session.on_packet_received(pkt)
                    
            # INI ADALAH KUNCI UTAMA!
            # Paksa cicflowmeter untuk memuntahkan semua flow yang masih menggantung (termasuk SYN Flood tanpa FIN)
            session.garbage_collect(None)
            
            # Jika tidak ada file CSV yang dibuat (misal pcap kosong), langsung kembali
            if not os.path.exists(csv_file):
                return
        except Exception as e:
            logger.error(f"Failed to extract features from {pcap_file}: {e}")
            return
            
        try:
            df = pd.read_csv(csv_file)
            if len(df) == 0:
                return
                
            expected_features = [
                "Flow Duration", "Total Fwd Packets", "Total Length of Fwd Packets", 
                "Fwd Packet Length Max", "Bwd Packet Length Max", "Bwd Packet Length Min", 
                "Flow IAT Mean", "Flow IAT Min", "Bwd IAT Total", "Bwd IAT Mean", 
                "Fwd PSH Flags", "Fwd Packets/s", "Bwd Packets/s", "Max Packet Length", 
                "ACK Flag Count", "URG Flag Count", "Down/Up Ratio", 
                "Init_Win_bytes_forward", "Init_Win_bytes_backward", "min_seg_size_forward", 
                "Active Mean", "Active Std", "Idle Std"
            ]
            
            def norm_str(s):
                return s.lower().replace(" ", "").replace("_", "").replace("/", "")
            
            norm_expected = [norm_str(f) for f in expected_features]
            
            # Map kolom CSV ke 23 fitur
            df_columns_norm = [norm_str(c) for c in df.columns]
            
            feature_indices = []
            for exp in norm_expected:
                if exp in df_columns_norm:
                    feature_indices.append(df_columns_norm.index(exp))
                else:
                    feature_indices.append(-1)
            
            for index, row in df.iterrows():
                src_ip = "Unknown"
                dst_ip = "Unknown"
                for c in ["Src IP", "src_ip", "src ip", "SrcIP"]:
                    if c in df.columns: src_ip = row[c]
                for c in ["Dst IP", "dst_ip", "dst ip", "DstIP"]:
                    if c in df.columns: dst_ip = row[c]
                
                features_list = []
                for idx in feature_indices:
                    if idx != -1:
                        try:
                            features_list.append(float(row.iloc[idx]))
                        except ValueError:
                            features_list.append(0.0)
                    else:
                        features_list.append(0.0)
                        
                input_data = np.array([features_list], dtype=np.float32)
                
                start_time = time.time()
                
                # 1. Dapatkan Probabilitas dari Base Models (jika modelnya ada)
                prob_xgb = 0.0
                prob_lgb = 0.0
                prob_cat = 0.0
                
                if self.session_xgb:
                    out_xgb = self.session_xgb.run(None, {self.xgb_input: input_data})
                    # Ambil probabilitas kelas positif (1)
                    prob_xgb = float(out_xgb[1][0][1]) if len(out_xgb) > 1 else float(out_xgb[0][0])
                    
                if self.session_lgb:
                    out_lgb = self.session_lgb.run(None, {self.lgb_input: input_data})
                    prob_lgb = float(out_lgb[1][0][1]) if len(out_lgb) > 1 else float(out_lgb[0][0])
                    
                if self.session_cat:
                    out_cat = self.session_cat.run(None, {self.cat_input: input_data})
                    prob_cat = float(out_cat[1][0][1]) if len(out_cat) > 1 else float(out_cat[0][0])
                    
                # 2. Feed Probabilities ke Meta-Model (Stacking)
                meta_input_data = np.array([[prob_xgb, prob_lgb, prob_cat]], dtype=np.float32)
                ort_outs = self.session_meta.run(None, {self.meta_input: meta_input_data})
                
                latency = time.time() - start_time
                
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
                    logger.warning(f"[DDoS ALERT] Flow dari {src_ip} ke {dst_ip} terdeteksi sebagai ANOMALI/DDoS! (Prob: XGB={prob_xgb:.2f} LGB={prob_lgb:.2f} CAT={prob_cat:.2f}) (Latensi: {latency*1000:.2f}ms)")
                else:
                    logger.info(f"[AMAN] Flow dari {src_ip} ke {dst_ip} terpantau normal. (Prob: XGB={prob_xgb:.2f} LGB={prob_lgb:.2f} CAT={prob_cat:.2f}) (Latensi: {latency*1000:.2f}ms)")
                    
        except pd.errors.EmptyDataError:
            # Tidak ada paket valid di pcap ini, abaikan
            pass
        except Exception as e:
            logger.error(f"Error processing CSV: {e}")
        finally:
            if os.path.exists(pcap_file): os.remove(pcap_file)
            if os.path.exists(csv_file): os.remove(csv_file)

    def watch_pcap_dir(self):
        logger.info("Micro-Batch Processor started. Menunggu traffic...")
        processed_files = set()
        
        while True:
            time.sleep(1)
            pcap_files = sorted(glob.glob(os.path.join(self.pcap_dir, "*.pcap")))
            
            if len(pcap_files) > 1:
                # File terakhir sedang ditulis tcpdump, proses file-file sebelumnya
                for pcap in pcap_files[:-1]:
                    if pcap not in processed_files:
                        processed_files.add(pcap)
                        self.extract_and_infer(pcap)
                        processed_files.remove(pcap)

    def start_tcpdump(self):
        # %H%M%S MENCEGAH OVERWRITE, ini yang membuat tcpdump sebelumnya gagal!
        # -Z root MENCEGAH Permission Denied (karena tcpdump otomatis drop root privileges)
        cmd = [
            "tcpdump", "-Z", "root", "-i", self.interface, "-n", "ip and (tcp or udp)",
            "-G", "5", "-w", os.path.join(self.pcap_dir, "traffic_%H%M%S.pcap")
        ]
        logger.info(f"Starting tcpdump on interface {self.interface}...")
        self.tcpdump_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        
        def log_tcpdump_stderr():
            for line in self.tcpdump_process.stderr:
                line = line.strip()
                if "listening on" in line:
                    logger.info(f"[TCPDUMP STARTED] {line}")
                else:
                    logger.error(f"[TCPDUMP] {line}")
                
        threading.Thread(target=log_tcpdump_stderr, daemon=True).start()
        
        self.watch_pcap_dir()

if __name__ == "__main__":
    nids = MicroBatchNIDS()
    nids.start_metric_reporter()
    try:
        nids.start_tcpdump()
    except KeyboardInterrupt:
        logger.info("Stopping NIDS...")
