# Kubernetes Manifest Templates

Full annotated manifests for GKE deployment. All `$VARIABLE` placeholders map to
the variables defined in the SKILL.md Step 1 table.

---

## PodDisruptionBudget

Ensures rolling updates don't take down all pods at once:

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: $APP_NAME-pdb
  namespace: $NAMESPACE
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: $APP_NAME
```

---

## Network Policy

Restrict pod-to-pod traffic to only what's needed:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: $APP_NAME-netpol
  namespace: $NAMESPACE
spec:
  podSelector:
    matchLabels:
      app: $APP_NAME
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: $NAMESPACE
    ports:
    - protocol: TCP
      port: 8080
  egress:
  - to: []   # Allow all egress (restrict as needed)
```

---

## ConfigMap

Non-sensitive configuration:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: $APP_NAME-config
  namespace: $NAMESPACE
data:
  APP_ENV: "production"
  LOG_LEVEL: "info"
  GCS_BUCKET: "$PROJECT_ID-$APP_NAME-storage"
  DB_HOST: "CLOUD_SQL_PRIVATE_IP"   # Replace after provisioning
  DB_NAME: "$APP_NAME"
  DB_PORT: "5432"
```

---

## BackendConfig (GCP-specific — health check tuning)

```yaml
apiVersion: cloud.google.com/v1
kind: BackendConfig
metadata:
  name: $APP_NAME-backendconfig
  namespace: $NAMESPACE
spec:
  healthCheck:
    checkIntervalSec: 15
    timeoutSec: 5
    healthyThreshold: 1
    unhealthyThreshold: 3
    type: HTTP
    requestPath: /healthz
    port: 8080
  connectionDraining:
    drainingTimeoutSec: 60
  sessionAffinity:
    affinityType: "CLIENT_IP"
```

Annotate your Service to use it:
```yaml
metadata:
  annotations:
    cloud.google.com/backend-config: '{"default": "$APP_NAME-backendconfig"}'
```

---

## FrontendConfig (HTTPS redirect)

```yaml
apiVersion: networking.gke.io/v1beta1
kind: FrontendConfig
metadata:
  name: $APP_NAME-frontendconfig
  namespace: $NAMESPACE
spec:
  redirectToHttps:
    enabled: true
    responseCodeName: MOVED_PERMANENTLY_DEFAULT
```

---

## Managed Certificate (GCP-managed TLS)

```yaml
apiVersion: networking.gke.io/v1
kind: ManagedCertificate
metadata:
  name: $APP_NAME-cert
  namespace: $NAMESPACE
spec:
  domains:
  - yourdomain.com   # Replace with actual domain
```

Annotate Ingress:
```yaml
metadata:
  annotations:
    networking.gke.io/managed-certificates: "$APP_NAME-cert"
    networking.gke.io/v1beta1.FrontendConfig: "$APP_NAME-frontendconfig"
```

---

## VPA (Vertical Pod Autoscaler) — optional

```yaml
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: $APP_NAME-vpa
  namespace: $NAMESPACE
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: $APP_NAME
  updatePolicy:
    updateMode: "Auto"
  resourcePolicy:
    containerPolicies:
    - containerName: $APP_NAME
      minAllowed:
        cpu: 100m
        memory: 128Mi
      maxAllowed:
        cpu: 4
        memory: 4Gi
```

---

## Cloud SQL Auth Proxy Sidecar

Add this alongside your main container if using Cloud SQL with private IP over proxy:

```yaml
- name: cloud-sql-proxy
  image: gcr.io/cloud-sql-connectors/cloud-sql-proxy:2
  args:
    - "--structured-logs"
    - "--port=5432"
    - "$PROJECT_ID:$REGION:$DB_NAME"
  securityContext:
    runAsNonRoot: true
  resources:
    requests:
      memory: "64Mi"
      cpu: "50m"
```