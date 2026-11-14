variable "project_id" {
  type        = string
  description = "GCP Project ID"
  default     = "finch-514709"
}

variable "billing_account_id" {
  type        = string
  description = "GCP Billing Account ID"
  default     = "01DEBA-ADE9E4-429DE9"
}

variable "region" {
  type        = string
  description = "Primary GCP region"
  default     = "asia-south1"
}

variable "zone" {
  type        = string
  description = "Primary GCP zone"
  default     = "asia-south1-a"
}

variable "machine_type" {
  type        = string
  description = "Compute Engine machine type"
  default     = "e2-standard-4"
}

variable "disk_size_gb" {
  type        = number
  description = "Boot SSD size in GB"
  default     = 100
}

variable "admin_ip_cidr" {
  type        = string
  description = "Admin public IP CIDR allowed for SSH alongside IAP"
  default     = "106.222.234.35/32"
}

variable "budget_amount_inr" {
  type        = number
  description = "Monthly budget limit in INR (~150 USD)"
  default     = 12500
}
