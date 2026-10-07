# Screenshot of the Feishu desktop window, optionally scrolling the chat first (WSL: call via powershell.exe).
#   powershell.exe -ExecutionPolicy Bypass -File feishu_shot.ps1 -Notches -80 -Out card.png   # to the newest message
#   -Notches N scrolls N wheel notches (negative = down); the PNG lands in %TEMP%. -X/-Y pick the scroll point
#   in 2000px-wide screenshot coordinates. The window must be restored (not minimised).
param([int]$Notches = 8, [int]$X = 1100, [int]$Y = 600, [string]$Out = "feishu.png")
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, int dx, int dy, int data, int extra);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
}
"@
[void][W]::SetProcessDPIAware()
$p = Get-Process Feishu | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
$h = $p.MainWindowHandle
$r = New-Object W+RECT; [void][W]::GetWindowRect($h, [ref]$r)
[void][W]::SetForegroundWindow($h); Start-Sleep -Milliseconds 400
$scale = 1.63
[void][W]::SetCursorPos([int]($r.L + $X * $scale), [int]($r.T + $Y * $scale)); Start-Sleep -Milliseconds 150
for ($i = 0; $i -lt [math]::Abs($Notches); $i++) { [W]::mouse_event(0x0800, 0, 0, [math]::Sign($Notches) * 120, 0); Start-Sleep -Milliseconds 60 }
Start-Sleep -Milliseconds 700
$w = $r.R - $r.L; $ht = $r.B - $r.T
$bmp = New-Object System.Drawing.Bitmap $w, $ht
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc(); [void][W]::PrintWindow($h, $hdc, 2); $g.ReleaseHdc($hdc)
$bmp.Save("$env:TEMP\$Out", [System.Drawing.Imaging.ImageFormat]::Png)
"saved $Out"
