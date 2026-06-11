variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP Region"
  type        = string
  default     = "asia-southeast2"
}

variable "cluster_name" {
  description = "GKE Cluster Name"
  type        = string
  default     = "envoy-ml-cluster"
}
