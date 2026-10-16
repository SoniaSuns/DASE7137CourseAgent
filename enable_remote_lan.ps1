# Run as administrator after configuring the explicit LAN interface/subnet.
$ErrorActionPreference = 'Stop'
$config = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'course_agent_app/chrome_operator_config.json') -Raw | ConvertFrom-Json).remote_workspace
& (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'course_agent_app/remote_workspace_admin.py') check-network
if ($LASTEXITCODE -ne 0) { throw 'Invalid explicit LAN configuration.' }
$interface = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $config.lan_address
if (-not $interface) { throw 'The configured LAN address is not assigned to this computer.' }
$profile = Get-NetConnectionProfile -InterfaceIndex $interface.InterfaceIndex
if ($profile.NetworkCategory -ne 'Private') { throw 'The chosen LAN must have the Private Windows network profile.' }
$name = 'DASE-RemoteWorkspace-LAN-' + $config.port
if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) {
    throw 'A rule with this name already exists. Inspect it before replacing it.'
}
New-NetFirewallRule -Name $name -DisplayName 'DASE remote workspace - trusted LAN' -Direction Inbound -Action Allow -Enabled True -Profile Private -Protocol TCP -LocalPort $config.port -LocalAddress $config.lan_address -RemoteAddress $config.lan_subnet -InterfaceAlias $interface.InterfaceAlias | Select-Object Name,Enabled,Profile,Direction,Action
