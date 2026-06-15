param(
    [string]$AwsProfile = "courseflow",
    [string]$AwsRegion = "ap-south-1",
    [string]$AllowedIp = "",
    [string]$BillingEmail = "",
    [string]$KeyOutputPath = "$HOME\.ssh\courseflow-key.pem"
)

$ErrorActionPreference = "Stop"
$Aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
if (-not (Test-Path -LiteralPath $Aws)) {
    $Aws = (Get-Command aws -ErrorAction Stop).Source
}
if (-not $AllowedIp) {
    $AllowedIp = (Invoke-RestMethod "https://checkip.amazonaws.com").Trim()
}
if ($AllowedIp -notmatch "^\d{1,3}(\.\d{1,3}){3}$") {
    throw "AllowedIp must be one IPv4 address without a CIDR suffix."
}

function Invoke-AwsJson {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $output = & $Aws @Arguments --profile $AwsProfile --region $AwsRegion --output json
    if ($LASTEXITCODE -ne 0) {
        throw "AWS CLI command failed: aws $($Arguments -join ' ')"
    }
    if ([string]::IsNullOrWhiteSpace($output)) {
        return $null
    }
    return $output | ConvertFrom-Json
}

function Invoke-AwsOptional {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Aws @Arguments --profile $AwsProfile --region $AwsRegion --output text 2>$null
        return [pscustomobject]@{
            Success = $LASTEXITCODE -eq 0
            Output = (($output | Out-String).Trim())
        }
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

$identity = Invoke-AwsJson sts get-caller-identity
$accountId = $identity.Account
$suffix = "$accountId-$AwsRegion"
$bucket = "courseflow-storage-$suffix"
$roleName = "courseflow-ec2-role"
$profileName = "courseflow-ec2-profile"
$policyName = "courseflow-s3-policy"
$securityGroupName = "courseflow-sg"
$keyName = "courseflow-key"

$vpcId = (& $Aws ec2 describe-vpcs --profile $AwsProfile --region $AwsRegion `
    --filters Name=is-default,Values=true --query "Vpcs[0].VpcId" --output text).Trim()
if (-not $vpcId -or $vpcId -eq "None") {
    throw "No default VPC found in $AwsRegion."
}

$securityGroupId = (& $Aws ec2 describe-security-groups --profile $AwsProfile --region $AwsRegion `
    --filters "Name=group-name,Values=$securityGroupName" "Name=vpc-id,Values=$vpcId" `
    --query "SecurityGroups[0].GroupId" --output text).Trim()
if (-not $securityGroupId -or $securityGroupId -eq "None") {
    $securityGroup = Invoke-AwsJson ec2 create-security-group `
        --group-name $securityGroupName `
        --description "CourseFlow production access" `
        --vpc-id $vpcId `
        --tag-specifications "ResourceType=security-group,Tags=[{Key=project,Value=courseflow},{Key=env,Value=demo}]"
    $securityGroupId = $securityGroup.GroupId
}

$rules = @(
    @{ Port = 22; Cidr = "$AllowedIp/32"; Description = "SSH from administrator IP" },
    @{ Port = 80; Cidr = "0.0.0.0/0"; Description = "HTTP redirect and ACME challenge" },
    @{ Port = 443; Cidr = "0.0.0.0/0"; Description = "HTTPS application traffic" },
    @{ Port = 5432; Cidr = "$AllowedIp/32"; Description = "PostgreSQL for local MCP" },
    @{ Port = 8000; Cidr = "$AllowedIp/32"; Description = "Direct API diagnostics" }
)
foreach ($rule in $rules) {
    $ingressResult = Invoke-AwsOptional ec2 authorize-security-group-ingress `
        --group-id $securityGroupId --protocol tcp --port $rule.Port --cidr $rule.Cidr `
        --tag-specifications "ResourceType=security-group-rule,Tags=[{Key=project,Value=courseflow},{Key=env,Value=demo}]"
    if (-not $ingressResult.Success) {
        Write-Host "Ingress rule for port $($rule.Port) already exists or could not be added."
    }
}

$keyLookup = Invoke-AwsOptional ec2 describe-key-pairs `
    --key-names $keyName --query "KeyPairs[0].KeyName"
if (-not $keyLookup.Success) {
    $keyMaterial = (& $Aws ec2 create-key-pair --profile $AwsProfile --region $AwsRegion `
        --key-name $keyName `
        --key-type ed25519 `
        --tag-specifications "ResourceType=key-pair,Tags=[{Key=project,Value=courseflow},{Key=env,Value=demo}]" `
        --query "KeyMaterial" --output text)
    $keyDirectory = Split-Path -Parent $KeyOutputPath
    New-Item -ItemType Directory -Force -Path $keyDirectory | Out-Null
    $keyText = ($keyMaterial | Out-String).Trim()
    $header = "-----BEGIN OPENSSH PRIVATE KEY-----"
    $footer = "-----END OPENSSH PRIVATE KEY-----"
    if ($keyText.StartsWith($header) -and $keyText.EndsWith($footer)) {
        $body = $keyText.Substring(
            $header.Length,
            $keyText.Length - $header.Length - $footer.Length
        ) -replace "\s", ""
        $wrappedBody = [regex]::Replace($body, ".{1,70}", '$0' + "`n").TrimEnd()
        $keyText = "$header`n$wrappedBody`n$footer`n"
    }
    [IO.File]::WriteAllText(
        $KeyOutputPath,
        $keyText,
        (New-Object System.Text.UTF8Encoding($false))
    )
}

$bucketLookup = Invoke-AwsOptional s3api head-bucket --bucket $bucket
if (-not $bucketLookup.Success) {
    & $Aws s3api create-bucket --profile $AwsProfile --region $AwsRegion `
        --bucket $bucket --create-bucket-configuration "LocationConstraint=$AwsRegion" | Out-Null
}
& $Aws s3api put-public-access-block --profile $AwsProfile --region $AwsRegion --bucket $bucket `
    --public-access-block-configuration `
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
& $Aws s3api put-bucket-tagging --profile $AwsProfile --region $AwsRegion --bucket $bucket `
    --tagging "TagSet=[{Key=project,Value=courseflow},{Key=env,Value=demo}]"

$trustPolicy = @{
    Version = "2012-10-17"
    Statement = @(@{
        Effect = "Allow"
        Principal = @{ Service = "ec2.amazonaws.com" }
        Action = "sts:AssumeRole"
    })
} | ConvertTo-Json -Depth 5 -Compress
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$trustFile = Join-Path $env:TEMP "courseflow-trust-policy.json"
[IO.File]::WriteAllText($trustFile, $trustPolicy, $utf8NoBom)
$trustFileUrl = "file://$($trustFile.Replace('\', '/'))"
$roleLookup = Invoke-AwsOptional iam get-role --role-name $roleName --query "Role.RoleName"
if (-not $roleLookup.Success) {
    & $Aws iam create-role --profile $AwsProfile --role-name $roleName `
        --assume-role-policy-document $trustFileUrl `
        --tags Key=project,Value=courseflow Key=env,Value=demo | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to create IAM role $roleName."
    }
}

$s3Policy = @{
    Version = "2012-10-17"
    Statement = @(
        @{
            Effect = "Allow"
            Action = @("s3:GetObject", "s3:PutObject", "s3:DeleteObject")
            Resource = "arn:aws:s3:::$bucket/*"
        },
        @{
            Effect = "Allow"
            Action = @("s3:ListBucket")
            Resource = "arn:aws:s3:::$bucket"
        }
    )
} | ConvertTo-Json -Depth 6 -Compress
$s3PolicyFile = Join-Path $env:TEMP "courseflow-s3-policy.json"
[IO.File]::WriteAllText($s3PolicyFile, $s3Policy, $utf8NoBom)
$s3PolicyFileUrl = "file://$($s3PolicyFile.Replace('\', '/'))"
& $Aws iam put-role-policy --profile $AwsProfile --role-name $roleName `
    --policy-name $policyName --policy-document $s3PolicyFileUrl
if ($LASTEXITCODE -ne 0) {
    throw "Unable to attach the S3 policy to $roleName."
}
& $Aws iam attach-role-policy --profile $AwsProfile --role-name $roleName `
    --policy-arn arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy
if ($LASTEXITCODE -ne 0) {
    throw "Unable to attach CloudWatchAgentServerPolicy to $roleName."
}

$instanceProfileLookup = Invoke-AwsOptional iam get-instance-profile `
    --instance-profile-name $profileName --query "InstanceProfile.InstanceProfileName"
if (-not $instanceProfileLookup.Success) {
    & $Aws iam create-instance-profile --profile $AwsProfile `
        --instance-profile-name $profileName `
        --tags Key=project,Value=courseflow Key=env,Value=demo | Out-Null
}
$profileRole = Invoke-AwsOptional iam get-instance-profile `
    --instance-profile-name $profileName --query "InstanceProfile.Roles[?RoleName=='$roleName'].RoleName | [0]"
if (-not $profileRole.Success -or -not $profileRole.Output -or $profileRole.Output -eq "None") {
    & $Aws iam add-role-to-instance-profile --profile $AwsProfile `
        --instance-profile-name $profileName --role-name $roleName
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to add $roleName to $profileName."
    }
    Start-Sleep -Seconds 10
}

$instanceId = (& $Aws ec2 describe-instances --profile $AwsProfile --region $AwsRegion `
    --filters Name=tag:project,Values=courseflow Name=instance-state-name,Values=pending,running,stopping,stopped `
    --query "Reservations[0].Instances[0].InstanceId" --output text).Trim()
if (-not $instanceId -or $instanceId -eq "None") {
    $amiId = (& $Aws ssm get-parameter --profile $AwsProfile --region $AwsRegion `
        --name /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id `
        --query "Parameter.Value" --output text).Trim()
    $subnetId = (& $Aws ec2 describe-subnets --profile $AwsProfile --region $AwsRegion `
        --filters "Name=vpc-id,Values=$vpcId" "Name=default-for-az,Values=true" `
        --query "Subnets[0].SubnetId" --output text).Trim()
    $instance = Invoke-AwsJson ec2 run-instances `
        --image-id $amiId `
        --instance-type t4g.small `
        --key-name $keyName `
        --security-group-ids $securityGroupId `
        --subnet-id $subnetId `
        --iam-instance-profile "Name=$profileName" `
        --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3,DeleteOnTermination=true}" `
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=courseflow},{Key=project,Value=courseflow},{Key=env,Value=demo}]" "ResourceType=volume,Tags=[{Key=project,Value=courseflow},{Key=env,Value=demo}]"
    $instanceId = $instance.Instances[0].InstanceId
}
& $Aws ec2 wait instance-running --profile $AwsProfile --region $AwsRegion --instance-ids $instanceId

$allocationId = (& $Aws ec2 describe-addresses --profile $AwsProfile --region $AwsRegion `
    --filters Name=tag:project,Values=courseflow --query "Addresses[0].AllocationId" --output text).Trim()
if (-not $allocationId -or $allocationId -eq "None") {
    $address = Invoke-AwsJson ec2 allocate-address `
        --domain vpc `
        --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=project,Value=courseflow},{Key=env,Value=demo}]"
    $allocationId = $address.AllocationId
}
$association = (& $Aws ec2 describe-addresses --profile $AwsProfile --region $AwsRegion `
    --allocation-ids $allocationId --query "Addresses[0].AssociationId" --output text).Trim()
if (-not $association -or $association -eq "None") {
    & $Aws ec2 associate-address --profile $AwsProfile --region $AwsRegion `
        --instance-id $instanceId --allocation-id $allocationId | Out-Null
}
$publicIp = (& $Aws ec2 describe-addresses --profile $AwsProfile --region $AwsRegion `
    --allocation-ids $allocationId --query "Addresses[0].PublicIp" --output text).Trim()

if ($BillingEmail) {
    $budget = @{
        Budget = @{
            BudgetName = "courseflow-monthly"
            BudgetLimit = @{ Amount = "20"; Unit = "USD" }
            TimeUnit = "MONTHLY"
            BudgetType = "COST"
        }
        NotificationsWithSubscribers = @(
            @{
                Notification = @{
                    NotificationType = "ACTUAL"
                    ComparisonOperator = "GREATER_THAN"
                    Threshold = 50
                    ThresholdType = "PERCENTAGE"
                }
                Subscribers = @(@{ SubscriptionType = "EMAIL"; Address = $BillingEmail })
            },
            @{
                Notification = @{
                    NotificationType = "ACTUAL"
                    ComparisonOperator = "GREATER_THAN"
                    Threshold = 100
                    ThresholdType = "PERCENTAGE"
                }
                Subscribers = @(@{ SubscriptionType = "EMAIL"; Address = $BillingEmail })
            },
            @{
                Notification = @{
                    NotificationType = "FORECASTED"
                    ComparisonOperator = "GREATER_THAN"
                    Threshold = 150
                    ThresholdType = "PERCENTAGE"
                }
                Subscribers = @(@{ SubscriptionType = "EMAIL"; Address = $BillingEmail })
            }
        )
    }
    $budgetFile = Join-Path $env:TEMP "courseflow-budget.json"
    [IO.File]::WriteAllText(
        $budgetFile,
        ($budget | ConvertTo-Json -Depth 8),
        $utf8NoBom
    )
    $budgetResult = Invoke-AwsOptional budgets create-budget --account-id $accountId `
        --cli-input-json "file://$budgetFile"
    if (-not $budgetResult.Success) {
        Write-Host "Budget already exists or the caller lacks Budgets permission."
    }
}

[pscustomobject]@{
    AccountId = $accountId
    Region = $AwsRegion
    InstanceId = $instanceId
    ElasticIp = $publicIp
    SecurityGroupId = $securityGroupId
    S3Bucket = $bucket
    KeyPath = $KeyOutputPath
} | Format-List
