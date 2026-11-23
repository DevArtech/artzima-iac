output "buckets" {
  description = "Bucket name -> id (not secret)"
  value       = { for k, b in b2_bucket.backup : b.bucket_name => b.bucket_id }
}

output "s3_endpoint" {
  description = "S3-compatible endpoint used by restic, barman-cloud and PBS"
  value       = "https://${local.cfg.b2_s3_endpoint}"
}
