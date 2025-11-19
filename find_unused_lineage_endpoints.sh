#!/bin/bash

endpoints=(
  "/schema"
  "/lineage/schema"
  "/sagemaker/pipelines"
  "/lineage"
  "/lineage/by-domain"
  "/datasets/schema/scan"
  "/datasets.*schema"
  "/sql/lineage"
  "/featurestore/feature-groups"
  "/tasks/sql/refresh"
  "/jobs/.*/sql-lineage"
  "/pipelines/.*/sql-lineage"
  "/tasks/sql/inline"
  "/api/v2/scan/rds-auto"
  "/api/v2/scan/cross-check"
)

echo "Unused Lineage API endpoints:"
echo "============================="

for endpoint in "${endpoints[@]}"; do
  result=$(grep -rE "$endpoint" ~/dspm \
    --exclude-dir=DSPM_DATA-Lineage-Tracking \
    --exclude-dir=.venv \
    --exclude-dir=node_modules \
    --exclude-dir=__pycache__ \
    --exclude="*.pyc" \
    2>/dev/null)
  
  if [ -z "$result" ]; then
    echo "❌ $endpoint"
  fi
done
