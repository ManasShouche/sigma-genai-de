#!/usr/bin/env bash
set -e

# ─────────────────────────────────────────────
# Sigma Command Center – Deployment Script
# ─────────────────────────────────────────────

AWS_ACCOUNT_ID="596234624522"
AWS_REGION="us-east-1"
ECR_REPO_NAME="sigma-command-center"
APP_RUNNER_SERVICE_NAME="sigma-command-center"
IMAGE_TAG="latest"
PORT=8501
INSTANCE_ROLE_ARN="arn:aws:iam::596234624522:role/sigma-lambda-role"
SIGMA_S3_BUCKET="sigma-datatech-ms"

ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE_URI="${ECR_REGISTRY}/${ECR_REPO_NAME}:${IMAGE_TAG}"

echo "========================================"
echo " Sigma Command Center – Deploy Starting"
echo "========================================"

# ── 1. Build Docker image ─────────────────────
echo ""
echo "[1/5] Building Docker image..."
docker build -t "${ECR_REPO_NAME}:${IMAGE_TAG}" .
echo "      Image built: ${ECR_REPO_NAME}:${IMAGE_TAG}"

# ── 2. Authenticate to ECR ────────────────────
echo ""
echo "[2/5] Authenticating to ECR..."
aws ecr get-login-password --region "${AWS_REGION}" \
    | docker login --username AWS --password-stdin "${ECR_REGISTRY}"
echo "      Authenticated to ${ECR_REGISTRY}"

# ── 3. Create ECR repo if it doesn't exist ────
echo ""
echo "[3/5] Ensuring ECR repository exists..."
if aws ecr describe-repositories \
        --repository-names "${ECR_REPO_NAME}" \
        --region "${AWS_REGION}" \
        --output text > /dev/null 2>&1; then
    echo "      Repository '${ECR_REPO_NAME}' already exists – skipping creation."
else
    echo "      Repository not found – creating '${ECR_REPO_NAME}'..."
    aws ecr create-repository \
        --repository-name "${ECR_REPO_NAME}" \
        --region "${AWS_REGION}" \
        --image-scanning-configuration scanOnPush=true \
        --output text > /dev/null
    echo "      Repository created."
fi

# ── 4. Tag and push image ─────────────────────
echo ""
echo "[4/5] Tagging and pushing image to ECR..."
docker tag "${ECR_REPO_NAME}:${IMAGE_TAG}" "${IMAGE_URI}"
docker push "${IMAGE_URI}"
echo "      Image pushed: ${IMAGE_URI}"

# ── 5. Create or update App Runner service ────
echo ""
echo "[5/5] Deploying to App Runner..."

# Check whether the service already exists
EXISTING_SERVICE_ARN=$(aws apprunner list-services \
    --region "${AWS_REGION}" \
    --query "ServiceSummaryList[?ServiceName=='${APP_RUNNER_SERVICE_NAME}'].ServiceArn" \
    --output text 2>/dev/null || true)

if [ -z "${EXISTING_SERVICE_ARN}" ]; then
    echo "      Service not found – creating '${APP_RUNNER_SERVICE_NAME}'..."

    SERVICE_ARN=$(aws apprunner create-service \
        --region "${AWS_REGION}" \
        --service-name "${APP_RUNNER_SERVICE_NAME}" \
        --source-configuration "{
            \"ImageRepository\": {
                \"ImageIdentifier\": \"${IMAGE_URI}\",
                \"ImageConfiguration\": {
                    \"Port\": \"${PORT}\",
                    \"RuntimeEnvironmentVariables\": {
                        \"SIGMA_S3_BUCKET\": \"${SIGMA_S3_BUCKET}\",
                        \"AWS_DEFAULT_REGION\": \"${AWS_REGION}\"
                    }
                },
                \"ImageRepositoryType\": \"ECR\"
            },
            \"AuthenticationConfiguration\": {
                \"AccessRoleArn\": \"${INSTANCE_ROLE_ARN}\"
            },
            \"AutoDeploymentsEnabled\": false
        }" \
        --instance-configuration "{
            \"Cpu\": \"0.25 vCPU\",
            \"Memory\": \"0.5 GB\",
            \"InstanceRoleArn\": \"${INSTANCE_ROLE_ARN}\"
        }" \
        --query "Service.ServiceArn" \
        --output text)

    echo "      Service created. ARN: ${SERVICE_ARN}"
    echo "      Waiting for service to reach RUNNING state..."

    aws apprunner wait service-running \
        --region "${AWS_REGION}" \
        --service-arn "${SERVICE_ARN}" 2>/dev/null || true

else
    echo "      Service exists (ARN: ${EXISTING_SERVICE_ARN}) – triggering re-deployment..."

    aws apprunner update-service \
        --region "${AWS_REGION}" \
        --service-arn "${EXISTING_SERVICE_ARN}" \
        --source-configuration "{
            \"ImageRepository\": {
                \"ImageIdentifier\": \"${IMAGE_URI}\",
                \"ImageConfiguration\": {
                    \"Port\": \"${PORT}\",
                    \"RuntimeEnvironmentVariables\": {
                        \"SIGMA_S3_BUCKET\": \"${SIGMA_S3_BUCKET}\",
                        \"AWS_DEFAULT_REGION\": \"${AWS_REGION}\"
                    }
                },
                \"ImageRepositoryType\": \"ECR\"
            },
            \"AuthenticationConfiguration\": {
                \"AccessRoleArn\": \"${INSTANCE_ROLE_ARN}\"
            }
        }" \
        --instance-configuration "{
            \"Cpu\": \"0.25 vCPU\",
            \"Memory\": \"0.5 GB\",
            \"InstanceRoleArn\": \"${INSTANCE_ROLE_ARN}\"
        }" \
        --output text > /dev/null

    SERVICE_ARN="${EXISTING_SERVICE_ARN}"
    echo "      Update triggered. ARN: ${SERVICE_ARN}"
    echo "      Waiting for service to reach RUNNING state..."

    aws apprunner wait service-running \
        --region "${AWS_REGION}" \
        --service-arn "${SERVICE_ARN}" 2>/dev/null || true
fi

# ── Print service URL ──────────────────────────
echo ""
echo "========================================"
SERVICE_URL=$(aws apprunner describe-service \
    --region "${AWS_REGION}" \
    --service-arn "${SERVICE_ARN}" \
    --query "Service.ServiceUrl" \
    --output text 2>/dev/null || echo "")

if [ -n "${SERVICE_URL}" ]; then
    echo " Deployment complete!"
    echo " App Runner service URL: https://${SERVICE_URL}"
else
    echo " Deployment triggered. Retrieve the URL with:"
    echo "   aws apprunner describe-service --region ${AWS_REGION} \\"
    echo "       --service-arn ${SERVICE_ARN} --query Service.ServiceUrl --output text"
fi
echo "========================================"
