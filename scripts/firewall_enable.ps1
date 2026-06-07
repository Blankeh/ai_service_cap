#Requires -RunAsAdministrator

$ruleName = "AI-Service-8000"
$existing = netsh advfirewall firewall show rule name=$ruleName 2>&1

if ($existing -match "No rules match") {
    netsh advfirewall firewall add rule `
        name=$ruleName `
        dir=in `
        action=allow `
        protocol=TCP `
        localport=8000 `
        remoteip=192.168.111.0/24
    Write-Host "[OK] Firewall rule '$ruleName' enabled (port 8000 open for local network)"
} else {
    Write-Host "[SKIP] Firewall rule '$ruleName' already exists"
}
