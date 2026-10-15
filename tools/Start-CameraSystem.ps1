# Khởi động các dịch vụ cục bộ. Nhập link riêng A/B/C/E trong Cài đặt trên web.
# Không nhập, lưu hay tự sao chép RTSP giữa các camera trong script này.
param([switch]$ServicesOnly)
$ErrorActionPreference = 'Stop'
$taskCameraRoot = Split-Path -Parent $PSScriptRoot
$taskPython = $env:CAMERA_PYTHON
if (-not $taskPython) { $taskPython = $env:CAMERA_DEMO_PYTHON }
if (-not $taskPython) {
    $taskPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
if (-not (Test-Path -LiteralPath $taskPython)) { $taskPython = (Get-Command python -ErrorAction Stop).Source }

function Test-CameraService([string]$Url) {
    try { return Invoke-RestMethod $Url -TimeoutSec 2 } catch { return $null }
}
function Start-DetachedCameraProcess([string]$Executable, [string[]]$Arguments) {
    # WMI tạo process ngoài phiên terminal đang chạy script. Đóng terminal
    # không kéo theo các worker camera/OCR/hồ sơ. Không cài task/service hệ thống.
    $taskCommandLine = (@($Executable) + $Arguments | ForEach-Object { '"' + $_ + '"' }) -join ' '
    $taskEnvironment = @()
    foreach ($taskName in @('SystemRoot','windir','PATH','USERPROFILE','LOCALAPPDATA','APPDATA','TEMP','TMP','VLC_EXE','FFMPEG_EXE')) {
        $taskValue = [Environment]::GetEnvironmentVariable($taskName)
        if ($taskValue) { $taskEnvironment += "$taskName=$taskValue" }
    }
    $taskStartup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
        ShowWindow = [uint16]0
        EnvironmentVariables = [string[]]$taskEnvironment
    }
    $taskLaunch = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = $taskCommandLine
        CurrentDirectory = $taskCameraRoot
        ProcessStartupInformation = $taskStartup
    }
    if ($taskLaunch.ReturnValue -ne 0) { throw "Không khởi động được dịch vụ (Windows $($taskLaunch.ReturnValue))." }
}
function Start-CameraWorker([string]$Service) {
    Start-DetachedCameraProcess $taskPython @((Join-Path $taskCameraRoot 'tools\run_camera_service.py'), '--service', $Service)
}

$taskHasher = [Security.Cryptography.SHA256]::Create()
try { $taskRootHash = [BitConverter]::ToString($taskHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskCameraRoot.ToLowerInvariant()))).Replace('-', '') }
finally { $taskHasher.Dispose() }
$taskMutex = [Threading.Mutex]::new($false, "Local\CameraSystem-$taskRootHash")
$taskHeld = $false
try {
    # F5/launcher và nút phục hồi không được tạo worker trùng cổng.
    try { $taskHeld = $taskMutex.WaitOne(30000) } catch [Threading.AbandonedMutexException] { $taskHeld = $true }
    if (-not $taskHeld) { throw 'Một phiên khác đang khởi động camera; thử lại sau.' }
    if (-not (Test-CameraService 'http://127.0.0.1:5083/status')) { Start-CameraWorker 'camera' }
    if (-not (Test-CameraService 'http://127.0.0.1:5084/status')) { Start-CameraWorker 'ocr' }
    if (-not (Test-CameraService 'http://127.0.0.1:5085/status')) { Start-CameraWorker 'records' }
    $taskReady = $false
    $taskDatabaseConfig = Join-Path $taskCameraRoot 'Data\database.json'
    $taskExpectedStorage = if (Test-Path -LiteralPath $taskDatabaseConfig) { Get-Content -LiteralPath $taskDatabaseConfig -Raw | ConvertFrom-Json } else { [pscustomobject]@{backend='sqlite'} }
    for ($taskAttempt = 0; $taskAttempt -lt 40; $taskAttempt++) {
        $taskCamera = Test-CameraService 'http://127.0.0.1:5083/status'
        $taskOcr = Test-CameraService 'http://127.0.0.1:5084/status'
        $taskRecords = Test-CameraService 'http://127.0.0.1:5085/status'
        $taskStorageReady = $true
        foreach ($taskService in @($taskCamera,$taskOcr,$taskRecords)) {
            if ($taskService.storage.backend -ne $taskExpectedStorage.backend) { $taskStorageReady = $false }
            if ($taskExpectedStorage.backend -eq 'sqlserver' -and ($taskService.storage.database -ne $taskExpectedStorage.database -or $taskService.storage.server -ne $taskExpectedStorage.server)) { $taskStorageReady = $false }
        }
        if ($taskStorageReady -and $taskCamera.available -and $taskOcr.available -and $taskOcr.recordingVersion -ge 2 -and $taskRecords.available -and $taskRecords.schemaVersion -ge 7) { $taskReady = $true; break }
        Start-Sleep -Milliseconds 250
    }
    if (-not $taskReady) { throw 'Camera/OCR/hồ sơ chưa sẵn sàng hoặc đang chạy phiên bản cũ. Xem log trong Data/service-logs.' }
} finally {
    if ($taskHeld) { $taskMutex.ReleaseMutex() }
    $taskMutex.Dispose()
}

# Worker phải sẵn sàng trước khi mở web. Nhả mutex trước khi web gọi
# hosted service để không khóa lẫn nhau khi khởi động từ một lệnh duy nhất.
if (-not $ServicesOnly) {
    if (-not (Test-CameraService 'http://127.0.0.1:5080/')) {
        $taskDll = Join-Path $taskCameraRoot 'artifacts\current-server\BridgeTrafficDemo.dll'
        if (-not (Test-Path -LiteralPath $taskDll)) { $taskDll = Join-Path $taskCameraRoot 'artifacts\demo-server\BridgeTrafficDemo.dll' }
        if (-not (Test-Path -LiteralPath $taskDll)) { throw 'Không tìm thấy máy chủ web đã được xác minh.' }
        Start-DetachedCameraProcess (Get-Command dotnet -ErrorAction Stop).Source @($taskDll, '--urls', 'http://127.0.0.1:5080')
    }
    $taskWebReady = $false
    for ($taskAttempt = 0; $taskAttempt -lt 40; $taskAttempt++) {
        if (Test-CameraService 'http://127.0.0.1:5080/') { $taskWebReady = $true; break }
        Start-Sleep -Milliseconds 250
    }
    if (-not $taskWebReady) { throw 'Web chưa sẵn sàng; kiểm tra log khởi động project.' }
}
Write-Output 'Sẵn sàng: http://127.0.0.1:5080/ — nhập RTSP từng ô trong Cài đặt.'
