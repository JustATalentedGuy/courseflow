#!/usr/bin/env bash
set -euo pipefail

: "${AWS_PROFILE:=courseflow}"
: "${AWS_REGION:=ap-south-1}"

INSTANCE_ID="$(aws ec2 describe-instances \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --filters Name=tag:project,Values=courseflow Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].InstanceId' \
  --output text)"

if [[ -n "$INSTANCE_ID" && "$INSTANCE_ID" != "None" ]]; then
  aws ec2 terminate-instances --profile "$AWS_PROFILE" --region "$AWS_REGION" --instance-ids $INSTANCE_ID
  aws ec2 wait instance-terminated --profile "$AWS_PROFILE" --region "$AWS_REGION" --instance-ids $INSTANCE_ID
fi

for ALLOCATION_ID in $(aws ec2 describe-addresses \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --filters Name=tag:project,Values=courseflow \
  --query 'Addresses[].AllocationId' \
  --output text); do
  aws ec2 release-address --profile "$AWS_PROFILE" --region "$AWS_REGION" --allocation-id "$ALLOCATION_ID"
done

for BUCKET in $(aws s3api list-buckets \
  --profile "$AWS_PROFILE" \
  --query "Buckets[?starts_with(Name, 'courseflow-storage-')].Name" \
  --output text); do
  aws s3 rb "s3://$BUCKET" --profile "$AWS_PROFILE" --region "$AWS_REGION" --force
done

echo "CourseFlow compute, Elastic IPs, and storage buckets removed."
echo "Review and remove the courseflow security group, IAM role, instance profile, log group, and budget if no longer needed."
