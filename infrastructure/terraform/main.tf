resource "google_compute_network" "vpc" {
  name                    = "${var.cluster_name}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${var.cluster_name}-subnet"
  network       = google_compute_network.vpc.id
  region        = var.region
  ip_cidr_range = "10.0.0.0/16"

  secondary_ip_range {
    range_name    = "pod-ranges"
    ip_cidr_range = "10.1.0.0/16"
  }
  secondary_ip_range {
    range_name    = "svc-ranges"
    ip_cidr_range = "10.2.0.0/20"
  }
}

resource "google_container_cluster" "primary" {
  name     = var.cluster_name
  location = var.region

  network    = google_compute_network.vpc.name
  subnetwork = google_compute_subnetwork.subnet.name

  ip_allocation_policy {
    cluster_secondary_range_name  = "pod-ranges"
    services_secondary_range_name = "svc-ranges"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  remove_default_node_pool = true
  initial_node_count       = 1
}

resource "google_container_node_pool" "primary_nodes" {
  name       = "${var.cluster_name}-node-pool"
  location   = var.region
  cluster    = google_container_cluster.primary.name
  node_count = 2

  node_config {
    machine_type = "e2-standard-4"
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
}
