param(
    [string]$AwsProfile = "courseflow",
    [string]$AwsRegion = "ap-south-1",
    [string]$DashboardName = "CourseFlow"
)

$ErrorActionPreference = "Stop"
$Aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
if (-not (Test-Path -LiteralPath $Aws)) {
    $Aws = (Get-Command aws -ErrorAction Stop).Source
}

$instanceId = (& $Aws ec2 describe-instances `
    --profile $AwsProfile `
    --region $AwsRegion `
    --filters Name=tag:project,Values=courseflow Name=instance-state-name,Values=running,stopped `
    --query "Reservations[0].Instances[0].InstanceId" `
    --output text).Trim()
if ($LASTEXITCODE -ne 0 -or -not $instanceId -or $instanceId -eq "None") {
    throw "No CourseFlow instance was found in $AwsRegion."
}

function New-MetricWidget {
    param(
        [int]$X,
        [string]$Title,
        [string]$MetricName,
        [string]$Label,
        [string]$Color,
        [string[]]$DimensionNames,
        [double]$Max = 100
    )

    $schema = (@("CWAgent") + $DimensionNames) -join ","
    return @{
        type = "metric"
        x = $X
        y = 0
        width = 8
        height = 6
        properties = @{
            region = $AwsRegion
            title = $Title
            view = "timeSeries"
            stacked = $false
            period = 60
            stat = "Average"
            yAxis = @{ left = @{ min = 0; max = $Max } }
            metrics = @(, @(
                @{
                    expression = "SEARCH('{$schema} MetricName=`"$MetricName`" InstanceId=`"$instanceId`"', 'Average', 60)"
                    label = $Label
                    id = "e1"
                    color = $Color
                }
            ))
        }
    }
}

$dashboard = @{
    widgets = @(
        (New-MetricWidget -X 0 -Title "CPU Active" -MetricName "cpu_usage_active" `
            -Label "CPU %" -Color "#2ca02c" -DimensionNames @("InstanceId", "cpu")),
        (New-MetricWidget -X 8 -Title "Memory Used" -MetricName "mem_used_percent" `
            -Label "Memory %" -Color "#1f77b4" -DimensionNames @("InstanceId")),
        (New-MetricWidget -X 16 -Title "Root Disk Used" -MetricName "disk_used_percent" `
            -Label "Disk %" -Color "#ff7f0e" `
            -DimensionNames @("InstanceId", "path", "device", "fstype")),
        @{
            type = "log"
            x = 0
            y = 6
            width = 24
            height = 8
            properties = @{
                region = $AwsRegion
                title = "Recent Application Errors"
                view = "table"
                query = "SOURCE '/courseflow/containers' | fields @timestamp, @message | filter @message like /ERROR|Exception|Traceback/ | sort @timestamp desc | limit 100"
            }
        }
    )
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$dashboardFile = Join-Path $env:TEMP "courseflow-cloudwatch-dashboard.json"
[IO.File]::WriteAllText(
    $dashboardFile,
    ($dashboard | ConvertTo-Json -Depth 12 -Compress),
    $utf8NoBom
)

& $Aws cloudwatch put-dashboard `
    --profile $AwsProfile `
    --region $AwsRegion `
    --dashboard-name $DashboardName `
    --dashboard-body "file://$($dashboardFile.Replace('\', '/'))"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to create CloudWatch dashboard $DashboardName."
}

Write-Host "CloudWatch dashboard $DashboardName now monitors $instanceId."
