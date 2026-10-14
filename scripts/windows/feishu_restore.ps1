# Bring a minimized Feishu window back so screenshots and clicks can see it (WSL: call via powershell.exe).
#   powershell.exe -ExecutionPolicy Bypass -File feishu_restore.ps1
Add-Type @"
using System; using System.Runtime.InteropServices;
public class R {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, int extra);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
}
"@
[void][R]::SetProcessDPIAware()
$p = Get-Process Feishu | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { "Feishu window not found"; exit 1 }
$h = $p.MainWindowHandle
if ([R]::IsIconic($h)) { [void][R]::ShowWindow($h, 9) }  # SW_RESTORE
[R]::keybd_event(0x12, 0, 0, 0); [void][R]::SetForegroundWindow($h); [R]::keybd_event(0x12, 0, 2, 0)
Start-Sleep -Milliseconds 600
$r = New-Object R+RECT; [void][R]::GetWindowRect($h, [ref]$r)
"restored iconic=$([R]::IsIconic($h)) rect=$($r.L),$($r.T),$($r.R),$($r.B)"
