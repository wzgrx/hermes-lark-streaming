# Minimize the Feishu window again after a test (WSL: call via powershell.exe).
#   powershell.exe -ExecutionPolicy Bypass -File feishu_minimize.ps1
Add-Type @"
using System; using System.Runtime.InteropServices;
public class M { [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c); }
"@
$p = Get-Process Feishu | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { "Feishu window not found"; exit 1 }
[void][M]::ShowWindow($p.MainWindowHandle, 6)
"minimized"
