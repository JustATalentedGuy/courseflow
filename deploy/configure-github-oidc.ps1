param(
    [string]$AwsProfile = "courseflow",
    [string]$AwsRegion = "ap-south-1",
    [string]$GitHubRepository = "JustATalentedGuy/courseflow",
    [string]$GitHubBranch = "main",
    [string]$SecurityGroupName = "courseflow-sg",
    [string]$RoleName = "courseflow-deploy"
)

$ErrorActionPreference = "Stop"
$Aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
if (-not (Test-Path -LiteralPath $Aws)) {
    $Aws = (Get-Command aws -ErrorAction Stop).Source
}

function Invoke-AwsText {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $output = & $Aws @Arguments --profile $AwsProfile --region $AwsRegion --output text
    if ($LASTEXITCODE -ne 0) {
        throw "AWS CLI command failed: aws $($Arguments -join ' ')"
    }
    return (($output | Out-String).Trim())
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

$accountId = Invoke-AwsText sts get-caller-identity --query Account
$securityGroupId = Invoke-AwsText ec2 describe-security-groups `
    --filters "Name=group-name,Values=$SecurityGroupName" `
    --query "SecurityGroups[0].GroupId"
if (-not $securityGroupId -or $securityGroupId -eq "None") {
    throw "Security group $SecurityGroupName was not found in $AwsRegion."
}

$providerUrl = "https://token.actions.githubusercontent.com"
$providerArn = "arn:aws:iam::$accountId`:oidc-provider/token.actions.githubusercontent.com"
$provider = Invoke-AwsOptional iam get-open-id-connect-provider `
    --open-id-connect-provider-arn $providerArn
if (-not $provider.Success) {
    & $Aws iam create-open-id-connect-provider --profile $AwsProfile `
        --url $providerUrl `
        --client-id-list sts.amazonaws.com `
        --tags Key=project,Value=courseflow Key=env,Value=demo | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to create the GitHub Actions OIDC provider."
    }
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$trustPolicy = @{
    Version = "2012-10-17"
    Statement = @(@{
        Effect = "Allow"
        Principal = @{ Federated = $providerArn }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = @{
            StringEquals = @{
                "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
                "token.actions.githubusercontent.com:sub" =
                    "repo:$GitHubRepository`:ref:refs/heads/$GitHubBranch"
            }
        }
    })
} | ConvertTo-Json -Depth 8
$trustFile = Join-Path $env:TEMP "courseflow-github-trust.json"
[IO.File]::WriteAllText($trustFile, $trustPolicy, $utf8NoBom)
$trustFileUrl = "file://$($trustFile.Replace('\', '/'))"

$role = Invoke-AwsOptional iam get-role --role-name $RoleName
if ($role.Success) {
    & $Aws iam update-assume-role-policy --profile $AwsProfile `
        --role-name $RoleName `
        --policy-document $trustFileUrl
}
else {
    & $Aws iam create-role --profile $AwsProfile `
        --role-name $RoleName `
        --assume-role-policy-document $trustFileUrl `
        --description "Temporary SSH ingress for CourseFlow GitHub deployments" `
        --max-session-duration 3600 `
        --tags Key=project,Value=courseflow Key=env,Value=demo | Out-Null
}
if ($LASTEXITCODE -ne 0) {
    throw "Unable to create or update IAM role $RoleName."
}

$securityGroupArn = "arn:aws:ec2:$AwsRegion`:$accountId`:security-group/$securityGroupId"
$permissionsPolicy = @{
    Version = "2012-10-17"
    Statement = @(
        @{
            Sid = "ManageCourseFlowSshIngress"
            Effect = "Allow"
            Action = @(
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:RevokeSecurityGroupIngress"
            )
            Resource = $securityGroupArn
        }
    )
} | ConvertTo-Json -Depth 8
$policyFile = Join-Path $env:TEMP "courseflow-github-permissions.json"
[IO.File]::WriteAllText($policyFile, $permissionsPolicy, $utf8NoBom)
$policyFileUrl = "file://$($policyFile.Replace('\', '/'))"
& $Aws iam put-role-policy --profile $AwsProfile `
    --role-name $RoleName `
    --policy-name courseflow-temporary-ssh `
    --policy-document $policyFileUrl
if ($LASTEXITCODE -ne 0) {
    throw "Unable to attach the temporary SSH policy to $RoleName."
}

$roleArn = "arn:aws:iam::$accountId`:role/$RoleName"
[pscustomobject]@{
    RoleArn = $roleArn
    SecurityGroupId = $securityGroupId
    GitHubSubject = "repo:$GitHubRepository`:ref:refs/heads/$GitHubBranch"
} | Format-List

Write-Host "Set these GitHub Actions variables:"
Write-Host "  gh variable set AWS_DEPLOY_ROLE_ARN --body `"$roleArn`""
Write-Host "  gh variable set AWS_SECURITY_GROUP_ID --body `"$securityGroupId`""
