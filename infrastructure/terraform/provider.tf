# infrastructure/terraform/provider.tf

terraform {
  required_version = ">= 1.3.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0" # Gunakan versi 5.x terbaru yang stabil
    }
  }

  # Sangat direkomendasikan untuk environment produksi/tim
  # backend "gcs" {
  #   bucket = "aqilix-gke-envoy-tfstate"
  #   prefix = "terraform/state/gke-envoy-ml"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
  # Autentikasi akan otomatis menggunakan kredensial aktif 
  # (via gcloud auth application-default login di lokal, atau Workload Identity/Service Account di CI/CD)
}
