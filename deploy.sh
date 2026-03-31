#!/bin/bash
# Deploy to Google Cloud Run
# Prerequisites: gcloud CLI installed and authenticated

PROJECT_ID="the-trial-of-df"
REGION="europe-west2"
SERVICE_NAME="slide-agent"
IMAGE="gcr.io/$PROJECT_ID/$SERVICE_NAME"

echo "Building Docker image..."
gcloud builds submit --tag $IMAGE --project $PROJECT_ID

echo "Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
  --image $IMAGE \
  --platform managed \
  --region $REGION \
  --project $PROJECT_ID \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 600 \
  --max-instances 3 \
  --set-env-vars "OPENROUTER_API_KEY=$(grep OPENROUTER_API_KEY .env | cut -d= -f2)" \
  --set-env-vars "GOOGLE_CLIENT_ID=$(grep GOOGLE_CLIENT_ID .env | cut -d= -f2)" \
  --set-env-vars "GOOGLE_CLIENT_SECRET=$(grep GOOGLE_CLIENT_SECRET .env | cut -d= -f2)" \
  --set-env-vars "GOOGLE_PROJECT_ID=$(grep GOOGLE_PROJECT_ID .env | cut -d= -f2)" \
  --set-env-vars "UNSPLASH_ACCESS_KEY=$(grep UNSPLASH_ACCESS_KEY .env | cut -d= -f2)" \
  --set-env-vars "FLASK_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')" \
  --set-env-vars "OAUTH_REDIRECT_URI=REPLACE_WITH_CLOUD_RUN_URL/auth/callback"

echo "Done! Service URL:"
gcloud run services describe $SERVICE_NAME --region $REGION --project $PROJECT_ID --format 'value(status.url)'
