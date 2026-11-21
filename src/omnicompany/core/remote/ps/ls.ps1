# omni remote ls: read-only directory listing; never descends through links/junctions.
$path = __OMNI_PATH__
$depth = [int]__OMNI_DEPTH__
$limit = [int]__OMNI_LIMIT__
$glob = __OMNI_GLOB__
$out = [ordered]@{ schema = 'omni.remote.ls/1'; ok = $false; path = $path; depth = $depth; entries = @(); returned = 0; truncated = $false; error = $null }
$entries = New-Object System.Collections.Generic.List[object]
function Describe($Item, [string]$Relative) {
  $isLink = ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
  $kind = 'file'
  if ($isLink) { $kind = 'link' } elseif ($Item.PSIsContainer) { $kind = 'dir' }
  $size = $null
  if (-not $Item.PSIsContainer) { $size = $Item.Length }
  return [ordered]@{ path = $Relative; name = $Item.Name; type = $kind; size = $size; mtime = $Item.LastWriteTimeUtc.ToString('o'); attributes = [string]$Item.Attributes }
}
try {
  if (-not (Test-Path -LiteralPath $path)) { throw "omni-remote: path not found: $path" }
  $root = Get-Item -LiteralPath $path -Force
  $out.path = $root.FullName
  if (-not $root.PSIsContainer) { $entries.Add((Describe $root $root.Name)) } else {
    $queue = New-Object System.Collections.Generic.Queue[object]
    $queue.Enqueue(@($root.FullName, 1))
    $prefix = $root.FullName.TrimEnd('\').Length + 1
    while ($queue.Count -gt 0 -and -not $out.truncated) {
      $current = $queue.Dequeue()
      foreach ($child in @(Get-ChildItem -LiteralPath $current[0] -Force -ErrorAction SilentlyContinue | Sort-Object @{ Expression = { -not $_.PSIsContainer } }, Name)) {
        $relative = $child.FullName.Substring($prefix)
        $isLink = ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        if (-not $glob -or $child.PSIsContainer -or $child.Name -like $glob) {
          if ($entries.Count -ge $limit) { $out.truncated = $true; break }
          $entries.Add((Describe $child $relative))
        }
        if ($child.PSIsContainer -and -not $isLink -and $current[1] -lt $depth) { $queue.Enqueue(@($child.FullName, $current[1] + 1)) }
      }
    }
  }
  $out.entries = $entries.ToArray()
  $out.returned = $entries.Count
  $out.ok = $true
} catch { $out.error = $_.Exception.Message }
Omni-Emit $out
if ($out.ok) { exit 0 } else { exit 2 }
