# omni remote grep: read-only content search. rg when the node has it, Select-String otherwise.
$pattern = __OMNI_PATTERN__
$path = __OMNI_PATH__
$glob = __OMNI_GLOB__
$ignoreCase = __OMNI_IGNORE_CASE__
$fixed = __OMNI_FIXED__
$offset = [int]__OMNI_OFFSET__
$max = [int]__OMNI_MAX__
$maxFiles = [int]__OMNI_MAX_FILES__
$maxLine = [int]__OMNI_MAX_LINE__
$engine = __OMNI_ENGINE__
$out = [ordered]@{ schema = 'omni.remote.grep/1'; ok = $false; engine = $null; pattern = $pattern; path = $path; glob = $glob
  offset = $offset; matches = @(); returned = 0; has_more = $false; files_scanned = 0; files_truncated = $false; error = $null }
$found = New-Object System.Collections.Generic.List[object]
$seen = 0
function Add-Match([string]$File, $Line, [string]$Text) {
  $script:seen++
  if ($script:seen -le $offset) { return $true }
  if ($found.Count -ge $max) { $out.has_more = $true; return $false }
  if ($Text.Length -gt $maxLine) { $Text = $Text.Substring(0, $maxLine) + ' ...[line truncated]' }
  $found.Add([ordered]@{ path = $File; line = [int]$Line; text = $Text })
  return $true
}
try {
  if (-not (Test-Path -LiteralPath $path)) { throw "omni-remote: path not found: $path" }
  $root = (Get-Item -LiteralPath $path -Force).FullName
  $out.path = $root
  $rg = $null
  if ($engine -ne 'select-string') { $rg = Get-Command rg.exe -ErrorAction SilentlyContinue }
  if ($engine -eq 'rg' -and -not $rg) { throw 'omni-remote: rg requested but not found on node' }
  if ($rg) {
    $out.engine = 'rg'
    $rgArgs = @('--line-number', '--no-heading', '--with-filename', '--color', 'never', '--no-messages')
    if ($ignoreCase) { $rgArgs += '--ignore-case' }
    if ($fixed) { $rgArgs += '--fixed-strings' }
    if ($glob) { $rgArgs += @('--glob', $glob) }
    $rgArgs += @('-e', $pattern, '--', $root)
    $ErrorActionPreference = 'Continue'
    foreach ($row in (& $rg.Source @rgArgs 2>$null | Select-Object -First ($offset + $max + 1))) {
      if ($row -match '^(.+?):(\d+):(.*)$') {
        if (-not (Add-Match $Matches[1] $Matches[2] $Matches[3])) { break }
      }
    }
    $ErrorActionPreference = 'Stop'
  } else {
    $out.engine = 'select-string'
    $files = New-Object System.Collections.Generic.List[string]
    $item = Get-Item -LiteralPath $root -Force
    if (-not $item.PSIsContainer) { $files.Add($item.FullName) } else {
      $stack = New-Object 'System.Collections.Generic.Stack[string]'
      $stack.Push($item.FullName)
      while ($stack.Count -gt 0 -and $files.Count -lt $maxFiles) {
        $dir = $stack.Pop()
        $children = @(Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue | Sort-Object Name)
        foreach ($child in $children) {
          if (($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
          if ($child.PSIsContainer) { if ($child.Name -ne '.git') { $stack.Push($child.FullName) }; continue }
          if ($glob) {
            $relative = $child.FullName.Substring($item.FullName.TrimEnd('\').Length + 1).Replace('\', '/')
            $g = $glob.Replace('\', '/')
            if ($g.StartsWith('**/')) { $g = $g.Substring(3) }
            if ($g.Contains('/')) { if (-not ($relative -like $g)) { continue } } elseif (-not ($child.Name -like $g)) { continue }
          }
          $files.Add($child.FullName)
          if ($files.Count -ge $maxFiles) { $out.files_truncated = $true; break }
        }
      }
    }
    $params = @{ Pattern = $pattern; Encoding = 'UTF8'; CaseSensitive = (-not $ignoreCase); SimpleMatch = [bool]$fixed }
    $stop = $false
    foreach ($file in $files) {
      $out.files_scanned++
      $head = $null
      try {
        $fs = [IO.File]::Open($file, 'Open', 'Read', 'ReadWrite')
        try { $head = New-Object byte[] ([Math]::Min(4096, $fs.Length)); [void]$fs.Read($head, 0, $head.Length) } finally { $fs.Dispose() }
      } catch { continue }
      if ($head -contains 0) { continue }
      foreach ($m in @(Select-String -LiteralPath $file @params -ErrorAction SilentlyContinue)) {
        if (-not (Add-Match $file $m.LineNumber $m.Line)) { $stop = $true; break }
      }
      if ($stop) { break }
    }
  }
  $out.matches = $found.ToArray()
  $out.returned = $found.Count
  $out.ok = $true
} catch { $out.error = $_.Exception.Message }
Omni-Emit $out
if ($out.ok) { exit 0 } else { exit 2 }
