# omni remote read: read-only, numbered lines, offset/limit or tail paging.
$path = __OMNI_PATH__
$start = [int]__OMNI_OFFSET__
$limit = [int]__OMNI_LIMIT__
$tail = [int]__OMNI_TAIL__
$maxLine = [int]__OMNI_MAX_LINE__
$maxChars = [int]__OMNI_MAX_CHARS__
$countLimitBytes = 64MB
$out = [ordered]@{ schema = 'omni.remote.read/1'; ok = $false; path = $path; size = $null; mtime = $null; binary = $false
  encoding = $null; start_line = $start; lines = @(); returned = 0; has_more = $false; total_lines = $null; truncated_lines = 0; error = $null }
try {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "omni-remote: not a file: $path" }
  $item = Get-Item -LiteralPath $path -Force
  $out.path = $item.FullName
  $out.size = $item.Length
  $out.mtime = $item.LastWriteTimeUtc.ToString('o')
  $fs = [IO.File]::Open($item.FullName, 'Open', 'Read', 'ReadWrite')
  try {
    $sniff = New-Object byte[] ([Math]::Min(8192, $fs.Length))
    [void]$fs.Read($sniff, 0, $sniff.Length)
    $isUtf16 = $sniff.Length -ge 2 -and (($sniff[0] -eq 0xFF -and $sniff[1] -eq 0xFE) -or ($sniff[0] -eq 0xFE -and $sniff[1] -eq 0xFF))
    if (-not $isUtf16 -and ($sniff -contains 0)) { $out.binary = $true; $out.ok = $true; throw 'binary' }
    [void]$fs.Seek(0, 'Begin')
    $approx = $false
    if ($tail -gt 0 -and $fs.Length -gt $countLimitBytes) { [void]$fs.Seek(-8MB, 'End'); $approx = $true }
    $reader = New-Object IO.StreamReader($fs, $OmniUtf8, $true)
    if ($approx) { [void]$reader.ReadLine() }
    $lines = New-Object System.Collections.Generic.List[object]
    $ring = New-Object System.Collections.Generic.Queue[object]
    $n = 0
    $chars = 0
    $stopCounting = $false
    while ($null -ne ($line = $reader.ReadLine())) {
      $n++
      if ($tail -gt 0) {
        $ring.Enqueue(@($n, $line))
        if ($ring.Count -gt $tail) { [void]$ring.Dequeue() }
        continue
      }
      if ($n -lt $start) { continue }
      if ($lines.Count -lt $limit -and $chars -lt $maxChars) {
        $lines.Add(@($n, $line))
        $chars += [Math]::Min($line.Length, $maxLine)
        continue
      }
      $out.has_more = $true
      if ($fs.Length -gt $countLimitBytes) { $stopCounting = $true; break }
    }
    if ($tail -gt 0) { foreach ($entry in $ring) { $lines.Add($entry) } }
    $out.encoding = $reader.CurrentEncoding.WebName
    if (-not $stopCounting -and -not $approx) { $out.total_lines = $n }
    $rendered = New-Object System.Collections.Generic.List[object]
    foreach ($entry in $lines) {
      $text = [string]$entry[1]
      if ($text.Length -gt $maxLine) { $text = $text.Substring(0, $maxLine) + ' ...[line truncated]'; $out.truncated_lines++ }
      $number = $entry[0]
      if ($approx) { $number = $null }
      $rendered.Add([ordered]@{ n = $number; text = $text })
    }
    $out.lines = $rendered.ToArray()  # not @($list): PS 5.1 throws "Argument types do not match" on lists of objects
    $out.returned = $rendered.Count
    if ($tail -gt 0) { $out.start_line = $(if ($rendered.Count -and -not $approx) { $rendered[0].n } else { $null }); $out.has_more = ($rendered.Count -gt 0 -and $rendered[0].n -gt 1) -or $approx }
    $out.ok = $true
  } finally { $fs.Dispose() }
} catch {
  if (-not ($out.binary -and $_.Exception.Message -eq 'binary')) { $out.error = $_.Exception.Message; $out.ok = $false }
}
Omni-Emit $out
if ($out.ok) { exit 0 } else { exit 2 }
