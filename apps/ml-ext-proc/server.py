import logging
from concurrent import futures
import grpc
import onnxruntime as ort
import numpy as np

# PENTING: Anda harus mengkompilasi file ext_proc.proto dari repositori Envoy
# menjadi file Python sebelum menjalankan ini.
# Perintah kompilasi: python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. ext_proc.proto
try:
    import ext_proc_pb2
    import ext_proc_pb2_grpc
except ImportError:
    logging.warning("Protobuf files belum dikompilasi. Mohon jalankan protoc terlebih dahulu.")

# Konfigurasi Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
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
                
                # --- LOGIKA INFERENSI ML (CONTOH) ---
                # 1. Ekstrak data dari headers/body
                # 2. Lakukan preprocessing ke format numpy array
                # dummy_input = np.random.randn(1, 10).astype(np.float32) 
                # 3. Jalankan inferensi ONNX
                # ort_inputs = {self.ort_session.get_inputs()[0].name: dummy_input}
                # ort_outs = self.ort_session.run(None, ort_inputs)
                
                # Jika ML memutuskan trafik AMAN (CONTINUE)
                response.request_headers.response.header_mutation.set_headers.add(
                    header=ext_proc_pb2.HeaderValue(key="x-ml-verdict", value="clean")
                )
                
                # Jika ML mendeteksi anomali, Anda bisa me-reject request di sini
                # dengan merespon ext_proc_pb2.ImmediateResponse(status=403)
                
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
