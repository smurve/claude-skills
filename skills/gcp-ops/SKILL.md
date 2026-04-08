---
name: gcp-gke-deploy
description: >
  Full-stack GCP infrastructure provisioning and GKE deployment skill. Use this skill
  whenever the user wants to: deploy an application to GKE (Google Kubernetes Engine),
  provision GCP resources (VPC, Cloud SQL, GCS, Artifact Registry, IAM, Load Balancer),
  generate Dockerfiles, write Kubernetes manifests, set up CI/CD pipelines for GCP,
  scale or maintain GKE workloads, or automate any part of the GCP → Docker → GKE
  workflow. Trigger even if the user only mentions "deploy to GCP", "GKE setup",
  "containerize my app", "create GCP infrastructure", or "write a Dockerfile for GCP".
  Uses claude-opus-4-6 for AI-assisted resource planning and manifest generation.
---

# GCP Full-Stack GKE Deployment Skill

Provisions all GCP infrastructure and deploys a containerized application to GKE.
Covers: VPC networking, Cloud SQL, GCS buckets, Artifact Registry, IAM, GKE cluster,
Kubernetes manifests, Dockerfile, and optional Cloud Build CI/CD.

## Overview of What This Skill Produces

1. **Dockerfile** — generic, multi-stage, language-agnostic base
2. **GCP Infrastructure** — via `gcloud` CLI commands (or Terraform if preferred)
3. **GKE Cluster + Node Pool** — with autoscaling
4. **Kubernetes Manifests** — Deployment, Service, Ingress, HPA, ConfigMap, Secret
5. **Full-stack resources** — VPC, Cloud SQL (Postgres), GCS bucket, Artifact Registry, IAM service accounts
6. **CI/CD config** — Cloud Build `cloudbuild.yaml`

---

## Step 1 — Gather Inputs

Before generating anything, collect:

| Variable       | Description                             | Example                 |
| -------------- | --------------------------------------- | ----------------------- |
| `PROJECT_ID`   | GCP project ID                          | `my-project-123`        |
| `REGION`       | GCP region                              | `europe-west6` (Zürich) |
| `ZONE`         | GCP zone                                | `europe-west6-a`        |
| `APP_NAME`     | Application name (lowercase, no spaces) | `myapp`                 |
| `IMAGE_TAG`    | Container image tag                     | `latest`                |
| `DB_NAME`      | Cloud SQL database name                 | `myapp-db`              |
| `CLUSTER_NAME` | GKE cluster name                        | `myapp-cluster`         |
| `NAMESPACE`    | Kubernetes namespace                    | `production`            |
| `MIN_NODES`    | Min nodes in node pool                  | `2`                     |
| `MAX_NODES`    | Max nodes in node pool                  | `10`                    |
| `MACHINE_TYPE` | GKE node machine type                   | `e2-standard-4`         |

If the user hasn't provided these, ask for `PROJECT_ID`, `APP_NAME`, and `REGION` at minimum.
Derive sensible defaults for the rest.

---

## Step 2 — Generate Dockerfile

Produce a generic multi-stage Dockerfile. Read `references/dockerfile-templates.md` for
language-specific variants if the user specifies a language.

**Generic base Dockerfile:**
```dockerfile
# ---- Build Stage ----
FROM debian:bookworm-slim AS builder
WORKDIR /app
COPY . .
# Add your build commands here, e.g.:
# RUN apt-get update && apt-get install -y <build-deps> && make build

# ---- Runtime Stage ----
FROM gcr.io/distroless/base-debian12 AS runtime
WORKDIR /app
COPY --from=builder /app/dist ./dist
EXPOSE 8080
USER nonroot:nonroot
ENTRYPOINT ["/app/dist/server"]
```

Also generate a `.dockerignore`:
```
.git
.gitignore
node_modules/
__pycache__/
*.pyc
*.log
.env
.env.*
dist/
coverage/
```

---

## Step 3 — Enable GCP APIs

```bash
gcloud services enable \
  container.googleapis.com \
  sqladmin.googleapis.com \
  storage.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  servicenetworking.googleapis.com \
  --project=$PROJECT_ID
```

---

## Step 4 — Networking (VPC)

```bash
# Create VPC
gcloud compute networks create $APP_NAME-vpc \
  --subnet-mode=custom \
  --project=$PROJECT_ID

# Create subnet
gcloud compute networks subnets create $APP_NAME-subnet \
  --network=$APP_NAME-vpc \
  --region=$REGION \
  --range=10.0.0.0/20 \
  --secondary-range=pods=10.4.0.0/14,services=10.0.32.0/20 \
  --project=$PROJECT_ID

# Firewall: allow internal
gcloud compute firewall-rules create $APP_NAME-allow-internal \
  --network=$APP_NAME-vpc \
  --allow=tcp,udp,icmp \
  --source-ranges=10.0.0.0/8 \
  --project=$PROJECT_ID

# Private services access (for Cloud SQL private IP)
gcloud compute addresses create google-managed-services-$APP_NAME \
  --global \
  --purpose=VPC_PEERING \
  --prefix-length=16 \
  --network=$APP_NAME-vpc \
  --project=$PROJECT_ID

gcloud services vpc-peerings connect \
  --service=servicenetworking.googleapis.com \
  --ranges=google-managed-services-$APP_NAME \
  --network=$APP_NAME-vpc \
  --project=$PROJECT_ID
```

---

## Step 5 — Artifact Registry

```bash
gcloud artifacts repositories create $APP_NAME-repo \
  --repository-format=docker \
  --location=$REGION \
  --description="Docker images for $APP_NAME" \
  --project=$PROJECT_ID

# Configure Docker auth
gcloud auth configure-docker $REGION-docker.pkg.dev

# Image path (use this throughout):
# $REGION-docker.pkg.dev/$PROJECT_ID/$APP_NAME-repo/$APP_NAME:$IMAGE_TAG
```

---

## Step 6 — IAM Service Accounts

```bash
# App service account (used by GKE workloads)
gcloud iam service-accounts create $APP_NAME-sa \
  --display-name="$APP_NAME Service Account" \
  --project=$PROJECT_ID

SA_EMAIL="$APP_NAME-sa@$PROJECT_ID.iam.gserviceaccount.com"

# Grant roles
for ROLE in \
  roles/cloudsql.client \
  roles/storage.objectAdmin \
  roles/secretmanager.secretAccessor \
  roles/artifactregistry.reader; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SA_EMAIL" \
    --role="$ROLE"
done

# Workload Identity binding (GKE → GSA)
gcloud iam service-accounts add-iam-policy-binding $SA_EMAIL \
  --role=roles/iam.workloadIdentityUser \
  --member="serviceAccount:$PROJECT_ID.svc.id.goog[$NAMESPACE/$APP_NAME-ksa]" \
  --project=$PROJECT_ID
```

---

## Step 7 — Cloud SQL (PostgreSQL)

```bash
gcloud sql instances create $DB_NAME \
  --database-version=POSTGRES_15 \
  --tier=db-g1-small \
  --region=$REGION \
  --network=$APP_NAME-vpc \
  --no-assign-ip \
  --availability-type=REGIONAL \
  --storage-auto-increase \
  --project=$PROJECT_ID

# Create database and user
gcloud sql databases create $APP_NAME \
  --instance=$DB_NAME \
  --project=$PROJECT_ID

DB_PASSWORD=$(openssl rand -base64 32)
gcloud sql users create $APP_NAME-user \
  --instance=$DB_NAME \
  --password=$DB_PASSWORD \
  --project=$PROJECT_ID

# Store DB password in Secret Manager
echo -n "$DB_PASSWORD" | gcloud secrets create $APP_NAME-db-password \
  --data-file=- \
  --project=$PROJECT_ID
```

---

## Step 8 — GCS Bucket (Storage)

```bash
gcloud storage buckets create gs://$PROJECT_ID-$APP_NAME-storage \
  --location=$REGION \
  --uniform-bucket-level-access \
  --project=$PROJECT_ID

# CORS config (if serving web assets)
cat > /tmp/cors.json <<EOF
[{
  "origin": ["*"],
  "method": ["GET", "HEAD"],
  "responseHeader": ["Content-Type"],
  "maxAgeSeconds": 3600
}]
EOF
gcloud storage buckets update gs://$PROJECT_ID-$APP_NAME-storage \
  --cors-file=/tmp/cors.json
```

---

## Step 9 — GKE Cluster

```bash
gcloud container clusters create $CLUSTER_NAME \
  --region=$REGION \
  --network=$APP_NAME-vpc \
  --subnetwork=$APP_NAME-subnet \
  --cluster-secondary-range-name=pods \
  --services-secondary-range-name=services \
  --enable-private-nodes \
  --master-ipv4-cidr=172.16.0.0/28 \
  --enable-ip-alias \
  --enable-autoscaling \
  --min-nodes=$MIN_NODES \
  --max-nodes=$MAX_NODES \
  --machine-type=$MACHINE_TYPE \
  --disk-size=50 \
  --enable-workload-identity \
  --workload-pool=$PROJECT_ID.svc.id.goog \
  --enable-shielded-nodes \
  --release-channel=stable \
  --project=$PROJECT_ID

# Get credentials
gcloud container clusters get-credentials $CLUSTER_NAME \
  --region=$REGION \
  --project=$PROJECT_ID
```

---

## Step 10 — Kubernetes Manifests

Generate all manifests. Read `references/k8s-manifests.md` for full annotated templates.

**Namespace + KSA:**
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: $NAMESPACE
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: $APP_NAME-ksa
  namespace: $NAMESPACE
  annotations:
    iam.gke.io/gcp-service-account: $APP_NAME-sa@$PROJECT_ID.iam.gserviceaccount.com
```

**Deployment:**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: $APP_NAME
  namespace: $NAMESPACE
spec:
  replicas: 2
  selector:
    matchLabels:
      app: $APP_NAME
  template:
    metadata:
      labels:
        app: $APP_NAME
    spec:
      serviceAccountName: $APP_NAME-ksa
      containers:
      - name: $APP_NAME
        image: $REGION-docker.pkg.dev/$PROJECT_ID/$APP_NAME-repo/$APP_NAME:$IMAGE_TAG
        ports:
        - containerPort: 8080
        resources:
          requests:
            cpu: "250m"
            memory: "256Mi"
          limits:
            cpu: "1000m"
            memory: "1Gi"
        env:
        - name: DB_PASSWORD
          valueFrom:
            secretKeyRef:
              name: $APP_NAME-secrets
              key: db-password
        - name: GCS_BUCKET
          value: $PROJECT_ID-$APP_NAME-storage
        livenessProbe:
          httpGet:
            path: /healthz
            port: 8080
          initialDelaySeconds: 15
          periodSeconds: 20
        readinessProbe:
          httpGet:
            path: /readyz
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 10
```

**Service + Ingress:**
```yaml
apiVersion: v1
kind: Service
metadata:
  name: $APP_NAME-svc
  namespace: $NAMESPACE
spec:
  selector:
    app: $APP_NAME
  ports:
  - port: 80
    targetPort: 8080
  type: ClusterIP
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: $APP_NAME-ingress
  namespace: $NAMESPACE
  annotations:
    kubernetes.io/ingress.class: "gce"
    kubernetes.io/ingress.global-static-ip-name: "$APP_NAME-ip"
spec:
  rules:
  - http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: $APP_NAME-svc
            port:
              number: 80
```

**HPA (autoscaling):**
```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: $APP_NAME-hpa
  namespace: $NAMESPACE
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: $APP_NAME
  minReplicas: 2
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

---

## Step 11 — Cloud Build CI/CD

```yaml
# cloudbuild.yaml
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - 'build'
      - '-t'
      - '$_REGION-docker.pkg.dev/$PROJECT_ID/$_APP_NAME-repo/$_APP_NAME:$COMMIT_SHA'
      - '.'

  - name: 'gcr.io/cloud-builders/docker'
    args:
      - 'push'
      - '$_REGION-docker.pkg.dev/$PROJECT_ID/$_APP_NAME-repo/$_APP_NAME:$COMMIT_SHA'

  - name: 'gcr.io/cloud-builders/kubectl'
    args:
      - 'set'
      - 'image'
      - 'deployment/$_APP_NAME'
      - '$_APP_NAME=$_REGION-docker.pkg.dev/$PROJECT_ID/$_APP_NAME-repo/$_APP_NAME:$COMMIT_SHA'
      - '-n'
      - '$_NAMESPACE'
    env:
      - 'CLOUDSDK_COMPUTE_REGION=$_REGION'
      - 'CLOUDSDK_CONTAINER_CLUSTER=$_CLUSTER_NAME'

substitutions:
  _REGION: europe-west6
  _APP_NAME: myapp
  _CLUSTER_NAME: myapp-cluster
  _NAMESPACE: production

options:
  logging: CLOUD_LOGGING_ONLY
```

---

## Step 12 — Secrets in Kubernetes

```bash
# Pull secret from Secret Manager and push into K8s
DB_PASSWORD=$(gcloud secrets versions access latest \
  --secret=$APP_NAME-db-password \
  --project=$PROJECT_ID)

kubectl create secret generic $APP_NAME-secrets \
  --from-literal=db-password="$DB_PASSWORD" \
  --namespace=$NAMESPACE
```

---

## AI-Assisted Resource Planning

When the user's requirements are ambiguous, use the Anthropic API (claude-opus-4-6) to
analyze the application description and recommend resource sizing, node pool configuration,
database tier, and network topology. See `references/ai-planning-prompt.md` for the
system prompt to use.

---

## Reference Files

- `references/dockerfile-templates.md` — language-specific Dockerfile variants (Python, Node, Go, Java)
- `references/k8s-manifests.md` — full annotated Kubernetes manifest templates
- `references/ai-planning-prompt.md` — system prompt for AI-assisted infrastructure planning
- `references/teardown.md` — commands to destroy all resources (for cleanup/cost management)