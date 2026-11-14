output "static_ip" {
  description = "Reserved static external IP of Finch VM"
  value       = google_compute_address.finch_static_ip.address
}

output "website_url" {
  description = "Public HTTP URL of the Finch research dashboard"
  value       = "http://${google_compute_address.finch_static_ip.address}/"
}

output "bucket_name" {
  description = "GCS data lake bucket in asia-south1"
  value       = google_storage_bucket.finch_lake.name
}

output "vm_name" {
  description = "Compute Engine VM name"
  value       = google_compute_instance.finch_vm.name
}

output "vm_zone" {
  description = "Compute Engine VM zone"
  value       = google_compute_instance.finch_vm.zone
}

output "service_account_email" {
  description = "Least-privilege service account attached to the VM"
  value       = google_service_account.finch_vm.email
}
