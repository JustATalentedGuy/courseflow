$ErrorActionPreference = "Stop"
$TaskName = "CourseFlow Local Transcript Fetcher"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed $TaskName."
} else {
    Write-Host "$TaskName is not installed."
}
