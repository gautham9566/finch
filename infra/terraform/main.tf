data "google_project" "current" {
  project_id = var.project_id
}

locals {
  apis = toset([
    "compute.googleapis.com",
    "storage.googleapis.com",
    "secretmanager.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
    "billingbudgets.googleapis.com",
    "bigquery.googleapis.com",
    "iap.googleapis.com",
    "iam.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ])
}

resource "google_project_service" "apis" {
  for_each           = local.apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_compute_address" "finch_static_ip" {
  name       = "finch-static-ip"
  region     = var.region
  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket" "finch_lake" {
  name                        = "${var.project_id}-mumbai-lake"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age            = 90
      matches_prefix = ["backups/"]
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_service_account" "finch_vm" {
  account_id   = "finch-vm-sa"
  display_name = "Finch Platform VM Least-Privilege SA"
  depends_on   = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "lake_object_admin" {
  bucket = google_storage_bucket.finch_lake.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.finch_vm.email}"
}

resource "google_project_iam_member" "vm_roles" {
  for_each = toset([
    "roles/secretmanager.secretAccessor",
    "roles/monitoring.viewer",
    "roles/monitoring.metricWriter",
    "roles/logging.logWriter",
    "roles/bigquery.dataViewer",
    "roles/bigquery.jobUser",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.finch_vm.email}"
}

resource "google_secret_manager_secret" "intraday_api_key" {
  secret_id = "finch-intraday-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_compute_firewall" "allow_web" {
  name    = "finch-allow-web"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["finch-vm"]
  depends_on    = [google_project_service.apis]
}

resource "google_compute_firewall" "allow_ssh" {
  name    = "finch-allow-ssh"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  # IAP tunnel range + explicit admin IP only
  source_ranges = ["35.235.240.0/20", var.admin_ip_cidr]
  target_tags   = ["finch-vm"]
  depends_on    = [google_project_service.apis]
}

resource "google_bigquery_dataset" "billing_export" {
  dataset_id                  = "billing_export"
  friendly_name               = "Cloud Billing Export"
  description                 = "Target dataset for GCP Cloud Billing export to BigQuery"
  location                    = var.region
  delete_contents_on_destroy  = false
  depends_on                  = [google_project_service.apis]
}

resource "google_billing_budget" "finch_budget" {
  billing_account = var.billing_account_id
  display_name    = "Finch Monthly Budget"

  budget_filter {
    projects = ["projects/${data.google_project.current.number}"]
  }

  amount {
    specified_amount {
      currency_code = "INR"
      units         = tostring(var.budget_amount_inr)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }

  depends_on = [google_project_service.apis]
}

resource "google_compute_instance" "finch_vm" {
  name         = "finch-vm"
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["finch-vm"]

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = var.disk_size_gb
      type  = "pd-ssd"
    }
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.finch_static_ip.address
    }
  }

  service_account {
    email  = google_service_account.finch_vm.email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  metadata_startup_script = <<-EOF
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -f /etc/finch_bootstrapped ]; then
      apt-get update -y
      DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 git curl jq postgresql-client
      systemctl enable --now docker
      mkdir -p /opt/finch /var/log/finch
      touch /etc/finch_bootstrapped
    fi
  EOF

  allow_stopping_for_update = true
  depends_on                = [google_project_service.apis]
}
