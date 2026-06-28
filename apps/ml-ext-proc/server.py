import logging
from concurrent import futures
import grpc
import onnxruntime as ort
import numpy as np
import time
import json
# PENTING: Anda harus mengkompilasi file ext_proc.proto dari repositori Envoy
# menjadi file Python sebelum menjalankan ini.
# Perintah kompilasi: python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. ext_proc.proto
try:
    from envoy.service.ext_proc.v3 import external_processor_pb2 as ext_proc_pb2
    from envoy.service.ext_proc.v3 import external_processor_pb2_grpc as ext_proc_pb2_grpc
    from envoy.config.core.v3 import base_pb2
except ImportError:
    logging.warning("Package 'xds-protos' belum terinstal. Mohon jalankan pip install xds-protos.")

import sys

# Konfigurasi Logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("ExtProc-ONNX")

class ExternalProcessorServicer(ext_proc_pb2_grpc.ExternalProcessorServicer):
    def __init__(self):
        # Muat model ONNX ke dalam memory saat server menyala
        self.model_path = "model.onnx"
        try:
            self.ort_session = ort.InferenceSession(self.model_path)
            logger.info(f"Model ONNX {self.model_path} berhasil dimuat.")
        except Exception as e:
            logger.error(f"Gagal memuat model ONNX: {e}")

    def Process(self, request_iterator, context):
        """
        Fungsi ini dipanggil oleh Envoy secara streaming.
        Sesuai konfigurasi di envoy.yaml, Envoy akan mengirim Header dan/atau Body.
        """
        for request in request_iterator:
            response = ext_proc_pb2.ProcessingResponse()

            # Deteksi jenis payload yang dikirim Envoy
            if request.HasField('request_headers'):
                logger.info("Menerima Request Headers dari Envoy.")
                
                # --- LOGIKA INFERENSI ML ---
                start_time = time.time()
                
                is_malicious = False
                try:
                    # 1. Ekstrak data dari headers
                    # PENTING: Sesuaikan ekstraksi fitur ini dengan bagaimana model Anda dilatih!
                    # Di sini kita mengekstrak beberapa fitur dummy untuk memenuhi dimensi (1, 10).
                    headers = request.request_headers.headers.headers
                    num_headers = len(headers)
                    user_agent_len = 0
                    path_len = 0
                    is_post = 0.0
                    
                    # Dictionary untuk menyimpan header mentah demi keperluan log inline
                    raw_headers_dict = {}
                    
                    for header in headers:
                        # Penting: Envoy versi terbaru sering menaruh isi header ke 'raw_value' (bytes) 
                        # ketimbang 'value' (string) untuk menghindari isu encoding UTF-8.
                        val = header.value
                        if not val and header.raw_value:
                            val = header.raw_value.decode('utf-8', 'ignore')
                            
                        raw_headers_dict[header.key] = val
                        
                        if header.key == "user-agent":
                            user_agent_len = len(val)
                        elif header.key == ":path":
                            path_len = len(val)
                        elif header.key == ":method":
                            is_post = 1.0 if val == "POST" else 0.0
                            
                    # 2. Preprocessing ke format numpy array
                    # Model mengharapkan input dengan shape (1, 3) berdasarkan log error
                    features = [num_headers, user_agent_len, path_len]
                    
                    # --- DEBUGGING LOGS (INLINE) ---
                    # Menyatukan semua info menjadi satu baris log agar rapi saat stress test
                    import json
                    logger.info(f"DEBUG_REQ | ONNX_Input: {features} | Headers: {json.dumps(raw_headers_dict)}")
                    
                    input_data = np.array([features], dtype=np.float32)
                    
                    # 3. Jalankan inferensi ONNX
                    if hasattr(self, 'ort_session'):
                        input_name = self.ort_session.get_inputs()[0].name
                        ort_outs = self.ort_session.run(None, {input_name: input_data})
                        
                        prediction = ort_outs[0][0] # Ambil nilai prediksi
                        
                        # Asumsi: Jika probabilitas > 0.5 atau klasifikasi = 1, maka itu DDoS
                        if isinstance(prediction, (np.ndarray, list)):
                            if len(prediction) > 1 and prediction[1] > 0.5:
                                is_malicious = True
                        else:
                            if prediction > 0.5:
                                is_malicious = True
                                
                        logger.info(f"Inferensi Selesai - Prediksi: {prediction} | Malicious: {is_malicious}")
                    else:
                        logger.warning("ONNX Session belum dimuat, tidak bisa melakukan klasifikasi.")
                except Exception as e:
                    logger.error(f"Gagal melakukan inferensi ML: {e}")
                
                # Hitung durasi (Calculate duration)
                latency = time.time() - start_time
                
                # Log latensi dengan format JSON standar untuk ekstraksi data riset/paper
                experiment_data = {
                    "metric_type": "inference_latency",
                    "unit": "seconds",
                    "value": round(latency, 6),
                    "timestamp": time.time()
                }
                logger.info(f"EXPERIMENT_DATA | {json.dumps(experiment_data)}")
                
                # 4. Berikan Rekomendasi ke Envoy
                if is_malicious:
                    # Jika ML mendeteksi anomali/DDoS, blokir dengan membalas HTTP 403 Forbidden
                    logger.warning("DDoS Terdeteksi! Memerintahkan Envoy untuk menolak request (HTTP 403).")
                    response.immediate_response.status.code = 403
                    response.immediate_response.details = "Blocked by ML-Ext-Proc: DDoS Detected"
                    response.immediate_response.headers.set_headers.add(
                        header=base_pb2.HeaderValue(key="x-ml-verdict", value="blocked-ddos")
                    )
                else:
                    # Jika ML memutuskan trafik AMAN (CONTINUE)
                    logger.info("Trafik Aman. Membiarkan request diteruskan.")
                    response.request_headers.response.header_mutation.set_headers.add(
                        header=base_pb2.HeaderValue(key="x-ml-verdict", value="clean")
                    )
                
            elif request.HasField('request_body'):
                logger.info("Menerima Request Body dari Envoy.")
                # Lakukan pemrosesan ONNX untuk payload body jika diperlukan
                pass
                
            yield response

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    # Daftarkan class ke gRPC server
    try:
        ext_proc_pb2_grpc.add_ExternalProcessorServicer_to_server(ExternalProcessorServicer(), server)
    except NameError:
         logger.error("Gagal mendaftarkan service. Pastikan file protobuf hasil kompilasi tersedia.")
         return

    # Sesuai dengan konfigurasi di k8s/01-configmap-envoy.yaml
    server.add_insecure_port('[::]:50051')
    server.start()
    logger.info("Ext-Proc gRPC Server berjalan di port 50051...")
    
    server.wait_for_termination()

if __name__ == '__main__':
    serve()
