# Click one point of the Feishu window, e.g. to expand a card panel (WSL: call via powershell.exe).
#   powershell.exe -ExecutionPolicy Bypass -File feishu_click.ps1 -X 1120 -Y 669
# -X/-Y are 2000px-wide screenshot coordinates. Refuses unless Feishu really is the foreground window.
param([Parameter(Mandatory = $true)][int]$X, [Parameter(Mandatory = $true)][int]$Y)
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public class F {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, int dx, int dy, int data, int extra);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, int extra);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
}
"@
[void][F]::SetProcessDPIAware()
$p = Get-Process Feishu | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { "Feishu window not found"; exit 1 }
$h = $p.MainWindowHandle
[F]::keybd_event(0x12, 0, 0, 0); [void][F]::SetForegroundWindow($h); [F]::keybd_event(0x12, 0, 2, 0)
Start-Sleep -Milliseconds 500
if ([F]::GetForegroundWindow() -ne $h) { "Feishu is not in front; not clicked"; exit 2 }
$r = New-Object F+RECT; [void][F]::GetWindowRect($h, [ref]$r)
$scale = 1.63
[void][F]::SetCursorPos([int]($r.L + $X * $scale), [int]($r.T + $Y * $scale)); Start-Sleep -Milliseconds 150
[F]::mouse_event(0x0002, 0, 0, 0, 0); [F]::mouse_event(0x0004, 0, 0, 0, 0); Start-Sleep -Milliseconds 800
"clicked"
