# omni remote: shared prelude for every remote operation (plain text, no encoded payloads).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$OmniUtf8 = New-Object System.Text.UTF8Encoding($false)
$OmniUtf8Bom = New-Object System.Text.UTF8Encoding($true)
[Console]::OutputEncoding = $OmniUtf8
$OutputEncoding = $OmniUtf8
$OmniOwnedRoots = @(__OMNI_OWNED_ROOTS__)
$OmniWriteRoots = @(__OMNI_WRITE_ROOTS__)

function Omni-Under([string]$Path, [string[]]$Roots) {
  $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
  foreach ($root in $Roots) {
    if (-not $root) { continue }
    $rf = [IO.Path]::GetFullPath($root).TrimEnd('\')
    if ($full -ieq $rf -or $full.StartsWith($rf + '\', [StringComparison]::OrdinalIgnoreCase)) { return $true }
  }
  return $false
}

function Omni-AssertOwned([string]$Path) {
  if (-not (Omni-Under $Path $OmniOwnedRoots)) { throw "omni-remote: refused write outside staging/jobs/backup roots: $Path" }
  return [IO.Path]::GetFullPath($Path)
}

function Omni-AssertTarget([string]$Path) {
  if (-not (Omni-Under $Path $OmniWriteRoots)) { throw "omni-remote: refused write outside write_roots: $Path" }
  return [IO.Path]::GetFullPath($Path)
}

function Omni-AssertPlain([string]$Path) {
  $current = [IO.Path]::GetFullPath($Path)
  while ($current) {
    $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
    if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "omni-remote: reparse point preserved: $current" }
    $parent = [IO.Path]::GetDirectoryName($current.TrimEnd('\'))
    if (-not $parent -or $parent -eq $current) { break }
    $current = $parent
  }
  $leaf = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  if ($leaf -and -not $leaf.PSIsContainer) {
    $links = @(& fsutil.exe hardlink list $leaf.FullName 2>$null)
    if ($LASTEXITCODE -ne 0 -or $links.Count -ne 1) { throw "omni-remote: hard-linked or unverifiable file preserved: $($leaf.FullName)" }
  }
  return [IO.Path]::GetFullPath($Path)
}

function Omni-AssertTreePlain([string]$Root) {
  Omni-AssertPlain $Root | Out-Null
  $item = Get-Item -LiteralPath $Root -Force
  if (-not $item.PSIsContainer) { return }
  $stack = New-Object 'System.Collections.Generic.Stack[string]'
  $stack.Push($item.FullName)
  while ($stack.Count -gt 0) {
    foreach ($child in @(Get-ChildItem -LiteralPath ($stack.Pop()) -Force)) {
      if (($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "omni-remote: reparse point preserved: $($child.FullName)" }
      if ($child.PSIsContainer) { $stack.Push($child.FullName) } else { Omni-AssertPlain $child.FullName | Out-Null }
    }
  }
}

# Delete exactly the listed files/dirs of a stage this tool created; refuse anything unknown.
function Omni-RemoveOwnedStage([string]$Stage, [string[]]$Files, [string[]]$Directories) {
  $full = Omni-AssertOwned $Stage
  Omni-AssertPlain $full | Out-Null
  if (-not (Test-Path -LiteralPath $full)) { return 'absent' }
  $expectedFiles = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
  $expectedDirs = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
  $prefix = $full.TrimEnd('\') + '\'
  foreach ($relative in $Files) {
    $target = [IO.Path]::GetFullPath((Join-Path $full $relative))
    if (-not $target.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw "omni-remote: cleanup entry escapes stage: $target" }
    [void]$expectedFiles.Add($target)
  }
  foreach ($relative in $Directories) {
    $target = [IO.Path]::GetFullPath((Join-Path $full $relative))
    if (-not $target.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw "omni-remote: cleanup directory escapes stage: $target" }
    [void]$expectedDirs.Add($target)
  }
  $stack = New-Object 'System.Collections.Generic.Stack[string]'
  $stack.Push($full)
  while ($stack.Count -gt 0) {
    foreach ($child in @(Get-ChildItem -LiteralPath ($stack.Pop()) -Force)) {
      if (($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "omni-remote: reparse point preserved: $($child.FullName)" }
      if ($child.PSIsContainer) {
        if (-not $expectedDirs.Contains($child.FullName)) { throw "omni-remote: unknown directory preserved; cleanup refused: $($child.FullName)" }
        $stack.Push($child.FullName)
      } elseif (-not $expectedFiles.Contains($child.FullName)) {
        throw "omni-remote: unknown file preserved; cleanup refused: $($child.FullName)"
      }
    }
  }
  foreach ($target in $expectedFiles) {
    if (Test-Path -LiteralPath $target) { Omni-AssertPlain $target | Out-Null; [IO.File]::Delete($target) }
  }
  foreach ($target in @($expectedDirs | Sort-Object Length -Descending)) {
    if (Test-Path -LiteralPath $target) { [IO.Directory]::Delete($target, $false) }
  }
  [IO.Directory]::Delete($full, $false)
  return 'removed'
}

function Omni-ErrorInfo($Err, [string]$Phase) {
  $ii = $Err.InvocationInfo
  $info = [ordered]@{
    phase = $Phase
    message = [string]$Err.Exception.Message
    type = $Err.Exception.GetType().FullName
    id = [string]$Err.FullyQualifiedErrorId
    script = $null
    line = $null
    column = $null
    line_text = $null
  }
  if ($ii) {
    $info.script = $ii.ScriptName
    $info.line = $ii.ScriptLineNumber
    $info.column = $ii.OffsetInLine
    if ($ii.Line) { $info.line_text = $ii.Line.Trim() }
  }
  return $info
}

function Omni-SameBytes([string]$A, [string]$B) {
  # Direct content comparison of two files. No digests: C:/workspace/AGENTS.md「版本依据」forbids judging
  # "same content" by a computed hash. Shared-read opens; both buffers are cleared before each read so the
  # whole-buffer comparison only ever sees the bytes just read.
  $fa = [IO.File]::Open($A, 'Open', 'Read', 'ReadWrite')
  $fb = [IO.File]::Open($B, 'Open', 'Read', 'ReadWrite')
  try {
    if ($fa.Length -ne $fb.Length) { return $false }
    $ba = New-Object byte[] 1048576
    $bb = New-Object byte[] 1048576
    while ($true) {
      [Array]::Clear($ba, 0, $ba.Length); [Array]::Clear($bb, 0, $bb.Length)
      $na = $fa.Read($ba, 0, $ba.Length)
      if ($na -le 0) { return $true }
      $nb = 0
      while ($nb -lt $na) { $r = $fb.Read($bb, $nb, $na - $nb); if ($r -le 0) { return $false }; $nb += $r }
      if (-not [Linq.Enumerable]::SequenceEqual([byte[]]$ba, [byte[]]$bb)) { return $false }
    }
  } finally { $fa.Dispose(); $fb.Dispose() }
}

function Omni-AssertSize([string]$Path, [long]$Expected, [string]$What) {
  # Truncation check for a delivered transport file (byte count, not a digest); the local source stays intact.
  $got = (Get-Item -LiteralPath $Path -Force).Length
  if ($got -ne $Expected) { throw "omni-remote: $What size differs (got $got bytes, sent $Expected): $Path" }
}

# Last N lines of a (possibly still growing) log. Not Get-Content -Tail: it can hang on PS 5.1. Shared-read open,
# only the last 4 MB are scanned. Emits nothing for a missing file.
function Omni-TailLines([string]$Path, [int]$Count) {
  if ($Count -le 0 -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
  $stream = [IO.File]::Open($Path, 'Open', 'Read', 'ReadWrite, Delete')
  try {
    $skipFirst = $false
    if ($stream.Length -gt 4MB) { [void]$stream.Seek(-4MB, 'End'); $skipFirst = $true }
    $reader = New-Object IO.StreamReader($stream, $OmniUtf8, $true)
    if ($skipFirst) { [void]$reader.ReadLine() }
    $ring = New-Object 'System.Collections.Generic.Queue[string]'
    while ($null -ne ($line = $reader.ReadLine())) {
      $ring.Enqueue($line)
      if ($ring.Count -gt $Count) { [void]$ring.Dequeue() }
    }
    return $ring.ToArray()
  } finally { $stream.Dispose() }
}

function Omni-Emit($Object) {
  [Console]::Out.WriteLine('OMNI-REMOTE-RESULT ' + ($Object | ConvertTo-Json -Depth 8 -Compress))
}

function Omni-AliveProcess($ProcessIdValue, [string]$CreatedTicks) {
  if (-not $ProcessIdValue -or -not $CreatedTicks) { return $null }
  $p = Get-CimInstance Win32_Process -Filter ("ProcessId=" + [int]$ProcessIdValue) -ErrorAction SilentlyContinue
  if (-not $p) { return $null }
  if ([math]::Abs($p.CreationDate.ToUniversalTime().Ticks - [int64]::Parse($CreatedTicks)) -gt 20000000) { return $null }
  return $p
}

# Descendants of a verified root, parents first; a child must be created after its parent (pid reuse guard).
function Omni-Descendants($Root) {
  $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
  $out = New-Object System.Collections.Generic.List[object]
  $queue = New-Object System.Collections.Generic.Queue[object]
  $queue.Enqueue($Root)
  while ($queue.Count -gt 0) {
    $parent = $queue.Dequeue()
    foreach ($child in $all) {
      if ($child.ParentProcessId -eq $parent.ProcessId -and $child.ProcessId -ne $parent.ProcessId -and $child.CreationDate -ge $parent.CreationDate) {
        $out.Add($child)
        $queue.Enqueue($child)
      }
    }
  }
  return $out
}
