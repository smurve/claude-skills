# Teardown — Destroy All GCP Resources

⚠️ **These commands are irreversible. Always confirm with the user before running.**

Run in this order to avoid dependency errors:

```bash
# 1. Delete GKE workloads first
kubectl delete namespace $NAMESPACE --ignore-not-found

# 2. Delete GKE cluster
gcloud container clusters delete $CLUSTER_NAME \
  --region=$REGION \
  --project=$PROJECT_ID \
  --quiet

# 3. Delete Cloud SQL
gcloud sql instances delete $DB_NAME \
  --project=$PROJECT_ID \
  --quiet

# 4. Delete GCS bucket (and all objects)
gcloud storage rm -r gs://$PROJECT_ID-$APP_NAME-storage

# 5. Delete Artifact Registry
gcloud artifacts repositories delete $APP_NAME-repo \
  --location=$REGION \
  --project=$PROJECT_ID \
  --quiet

# 6. Delete Secret Manager secrets
gcloud secrets delete $APP_NAME-db-password \
  --project=$PROJECT_ID \
  --quiet

# 7. Delete IAM service account
gcloud iam service-accounts delete \
  $APP_NAME-sa@$PROJECT_ID.iam.gserviceaccount.com \
  --project=$PROJECT_ID \
  --quiet

# 8. Delete VPC peering
gcloud services vpc-peerings delete \
  --service=servicenetworking.googleapis.com \
  --network=$APP_NAME-vpc \
  --project=$PROJECT_ID \
  --quiet

# 9. Delete subnet and VPC
gcloud compute networks subnets delete $APP_NAME-subnet \
  --region=$REGION \
  --project=$PROJECT_ID \
  --quiet

gcloud compute firewall-rules delete $APP_NAME-allow-internal \
  --project=$PROJECT_ID \
  --quiet

gcloud compute addresses delete google-managed-services-$APP_NAME \
  --global \
  --project=$PROJECT_ID \
  --quiet

gcloud compute networks delete $APP_NAME-vpc \
  --project=$PROJECT_ID \
  --quiet
```