
# Build Docker container so the image is available to other services when
#  they're first created.

resource "null_resource" "initial_container_build" {
  provisioner "local-exec" {
    working_dir = var.app_dir
    command     = <<EOT
    gcloud builds submit \
      --config cloudbuild.container.yaml \
      --substitutions="SHORT_SHA=latest,_IMAGE_NAME=${var.image_name}"
EOT
  }
}

resource "google_cloudbuild_trigger" "deploy" {
  provider = google-beta
  project  = var.project_id
  name     = "7-APPS-DEPLOYMENT"
  disabled = true

  ignored_files = ["dashboard/**", "docs/**", "terraform/**"]
  filename      = "app/cloudbuild.yaml"

  github {
    owner = var.github_owner
    name  = var.github_repo
    push {
      branch = var.github_branch
    }
  }
}

/* Cloud Scheduler ---------------------------------------------------------- */

# Reset the app every half-hour

resource "google_cloud_scheduler_job" "reset" {
  project     = var.project_id
  region      = var.region
  name        = "reset-app"
  description = "Reset application"
  schedule    = "*/30 * * * *"

  time_zone        = "Australia/Melbourne"
  attempt_deadline = "660s"

  http_target {
    http_method = "POST"

    uri = "https://cloudbuild.googleapis.com/v1/${google_cloudbuild_trigger.deploy.id}:run"
    body = base64encode(jsonencode({
      repoName   = var.github_branch,
      branchName = var.github_repo
    }))

    headers = {
      Content-Type = "application/json"
    }

    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

/* IAM ---------------------------------------------------------------------- */

# Create a service account for Cloud Scheduler

resource "google_service_account" "scheduler" {
  account_id   = "cloud-scheduler"
  display_name = "🤖 Cloud Scheduler service account"
  description  = "🕹️️️ Account for running Cloud Build jobs"
  project      = var.project_id
}

resource "google_project_iam_member" "scheduler" {
  project = var.project_id
  role    = "roles/cloudbuild.builds.editor"
  member  = "serviceAccount:${google_service_account.scheduler.email}"
}

# Give Cloud Build god mode over the project

resource "google_project_iam_member" "project" {
  for_each = toset(["roles/editor", "roles/iap.tunnelResourceAccessor"])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${data.google_project.project.number}@cloudbuild.gserviceaccount.com"
}
