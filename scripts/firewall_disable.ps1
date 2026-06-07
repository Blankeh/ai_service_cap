#Requires -RunAsAdministrator

$ruleName = "AI-Service-8000"
$existing = netsh advfirewall firewall show rule name=$ruleName 2>&1

if ($existing -match "No rules match") {
    Write-Host "[SKIP] Firewall rule '$ruleName' does not exist"
} else {
    netsh advfirewall firewall delete rule name=$ruleName
    Write-Host "[OK] Firewall rule '$ruleName' removed (port 8000 closed)"
}
