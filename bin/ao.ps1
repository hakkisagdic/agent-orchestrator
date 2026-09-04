<#
.SYNOPSIS
  agent-orchestrator - read-only observation on Windows, with nothing installed.

.DESCRIPTION
  Windows ships PowerShell and does not ship Python. That is the whole reason this
  file exists: on macOS and Linux `ao` already runs with nothing installed, because
  python3 is there.

  It is a deliberate SUBSET - status, board, doctor - and it will stay one.
  Everything else in `ao` either takes a decision (`commit-ok`), spends the machine
  (`verify`, `lock`), kills processes (`hold`) or speaks a protocol (`mcp`, `a2a`),
  and a second implementation of any of those is a second thing to be wrong. Two
  implementations drift; the way to make drift harmless is to keep the second one
  small enough that it cannot hide a disagreement.

  STATUS: written from the PowerShell language reference and reviewed, but not yet
  run on Windows - no Windows machine was available. Treat it as `documented`, the
  same bar the adapter registry uses, until someone runs it and says otherwise.

  For anything beyond looking: install the Python package.
      winget install Python.Python.3.12
      pip install agent-orchestrator

.EXAMPLE
  .\ao.ps1 status
  .\ao.ps1 board -Root C:\work\project
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)][ValidateSet('status', 'board', 'doctor')][string]$Command = 'status',
  [string]$Root
)

$ErrorActionPreference = 'Stop'

function Find-Root([string]$Start) {
  # An explicit path is taken at face value; the user knows where their project is.
  if ($Start) { return (Resolve-Path $Start).Path }
  $dir = (Get-Location).Path
  while ($dir) {
    if ((Test-Path (Join-Path $dir '.ao')) -or (Test-Path (Join-Path $dir '.git'))) { return $dir }
    $parent = Split-Path $dir -Parent
    if ($parent -eq $dir) { break }
    $dir = $parent
  }
  return (Get-Location).Path
}

function Get-Board([string]$Root) {
  # Same forgiving parse as the Python side: a board a human cannot hand-edit
  # during an incident goes stale during the incident it was built for.
  $path = Join-Path $Root '.ao\board.md'
  $out = [ordered]@{}
  if (-not (Test-Path $path)) { return $out }
  $state = $null
  foreach ($line in Get-Content $path) {
    if ($line -match '^##\s+([a-z]+)\s*$') { $state = $Matches[1]; $out[$state] = @(); continue }
    if (-not $state -or $line.TrimStart() -notlike '- *') { continue }
    if ($line.TrimStart().Substring(2).Trim() -match '^\[([^\]]+)\]\s*(.*)$') {
      $parts = $Matches[2] -split [char]0x00B7
      $notes = @{}
      # Guard the range: PowerShell's 1..0 counts *down*, so on a one-element
      # array this would read index 1 then 0 and file the title as a note.
      if ($parts.Count -gt 1) {
        foreach ($p in $parts[1..($parts.Count - 1)]) {
          $kv = $p -split ':', 2
          if ($kv.Count -eq 2) { $notes[$kv[0].Trim()] = $kv[1].Trim() }
        }
      }
      $out[$state] += [pscustomobject]@{ Id = $Matches[1]; Title = $parts[0].Trim(); Notes = $notes }
    }
  }
  return $out
}

function Get-Session([string]$Root) {
  # Kiro keeps one directory per workspace, named by an opaque hash, so the
  # workspace cannot be derived from the path - find the session whose recorded
  # cwd matches instead, and fall back to the most recently written one.
  $base = Join-Path $env:USERPROFILE '.kiro\sessions'
  if (-not (Test-Path $base)) { return $null }
  $best = $null
  foreach ($f in Get-ChildItem $base -Recurse -Filter 'messages.jsonl' -ErrorAction SilentlyContinue) {
    $meta = Join-Path $f.Directory 'session.json'
    $cwd = $null
    if (Test-Path $meta) {
      try { $cwd = (Get-Content $meta -Raw | ConvertFrom-Json).cwd } catch {}
    }
    if ($cwd -and ((Resolve-Path $cwd -ErrorAction SilentlyContinue).Path -eq $Root)) { return $f }
    if (-not $best -or $f.LastWriteTime -gt $best.LastWriteTime) { $best = $f }
  }
  return $best
}

function Show-Status([string]$Root) {
  $name = Split-Path $Root -Leaf
  Write-Host ([string][char]0x2550 * 78) -ForegroundColor Cyan
  Write-Host "  $($name.ToUpper())   agent-orchestrator   $(Get-Date -Format 'dd MMM HH:mm:ss')"
  Write-Host ([string][char]0x2550 * 78) -ForegroundColor Cyan

  $sess = Get-Session $Root
  if (-not $sess) {
    Write-Host "`nNo agent session found for this workspace." -ForegroundColor Yellow
    Write-Host "   $Root" -ForegroundColor DarkGray
    return
  }
  $age = [int]((Get-Date) - $sess.LastWriteTime).TotalSeconds

  # A fresh transcript is not a live turn: the file keeps its timestamp after the
  # process exits, so ask the process table before calling anything WORKING.
  # Weaker than the Python check, and deliberately so: matching a process to a
  # repository needs its cwd, which Windows does not expose without extra work.
  # This asks only whether an agent runtime is up at all, so it can say STOPPED
  # with confidence and WORKING only as a strong hint.
  $procs = @(Get-Process -Name 'kiro-cli', 'claude', 'node' -ErrorAction SilentlyContinue)
  if ($age -lt 120 -and $procs.Count -gt 0) { $state = 'WORKING'; $col = 'Green' }
  elseif ($age -lt 240 -and $procs.Count -gt 0) { $state = 'slowing'; $col = 'Yellow' }
  elseif ($procs.Count -eq 0 -and $age -lt 240) { $state = 'STOPPED'; $col = 'Red' }
  else { $state = 'IDLE'; $col = 'Red' }
  Write-Host "`n$state" -ForegroundColor $col -NoNewline
  Write-Host "  last write $([int]($age / 60))m $($age % 60)s ago" -ForegroundColor DarkGray

  Push-Location $Root
  try {
    $dirty = @(git status --porcelain 2>$null).Count
    $log = @(git log --oneline -3 2>$null)
    Write-Host "`n-- REPOSITORY " -ForegroundColor Magenta -NoNewline
    Write-Host ([string][char]0x2500 * 64) -ForegroundColor Magenta
    $log | ForEach-Object { Write-Host "   $_" }
    Write-Host "   $dirty files uncommitted" -ForegroundColor DarkGray
  } finally { Pop-Location }

  $board = Get-Board $Root
  if ($board.Keys.Count) {
    $counts = ($board.Keys | Where-Object { $board[$_].Count } |
               ForEach-Object { "$($board[$_].Count) $_" }) -join ' | '
    Write-Host "   Board:   $counts"
    foreach ($b in $board['blocked']) {
      $why = if ($b.Notes['needs']) { $b.Notes['needs'] } else { 'reason not recorded' }
      Write-Host "     x $($b.Id)  $($b.Title) - $why" -ForegroundColor Red
    }
  }
}

function Show-Board([string]$Root) {
  $board = Get-Board $Root
  if (-not $board.Keys.Count) {
    Write-Host "No board here. Create .ao\board.md - see docs/sources.md" -ForegroundColor Yellow
    return
  }
  # Blocked first: it is the state that goes unnoticed, because work moved past it
  # and nothing else looks wrong.
  foreach ($s in @('running', 'blocked', 'queued', 'inbox', 'verified', 'done')) {
    if (-not $board[$s] -or -not $board[$s].Count) { continue }
    $col = switch ($s) { 'running' { 'Green' } 'blocked' { 'Red' } 'verified' { 'Cyan' } default { 'Gray' } }
    Write-Host "`n$($s.ToUpper()) ($($board[$s].Count))" -ForegroundColor $col
    foreach ($i in $board[$s]) {
      $n = ($i.Notes.GetEnumerator() | ForEach-Object { "$($_.Key): $($_.Value)" }) -join '  '
      Write-Host "   $($i.Id)  $($i.Title)  " -NoNewline
      Write-Host $n -ForegroundColor DarkGray
    }
  }
}

function Show-Doctor([string]$Root) {
  Write-Host "root      $Root"
  foreach ($p in @('.ao', '.ao\board.md', '.ao\gates.json', 'agent-mail', 'semantic-review')) {
    $full = Join-Path $Root $p
    if (Test-Path $full) { Write-Host "ok        $p" -ForegroundColor Green }
    else { Write-Host "missing   $p" -ForegroundColor DarkGray }
  }
  $sess = Get-Session $Root
  if ($sess) { Write-Host "session   $($sess.Directory.Name)" -ForegroundColor Green }
  else { Write-Host "session   none found" -ForegroundColor Yellow }
  $py = Get-Command python, python3 -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($py) {
    Write-Host "python    $($py.Source)" -ForegroundColor Green
    Write-Host "          full ao available: pip install agent-orchestrator" -ForegroundColor DarkGray
  } else {
    Write-Host "python    not found - this script is the read-only subset" -ForegroundColor Yellow
    Write-Host "          verify, commit-ok, hold, watchdog, mcp and a2a need Python" -ForegroundColor DarkGray
    Write-Host "          winget install Python.Python.3.12; pip install agent-orchestrator" -ForegroundColor DarkGray
  }
}

$r = Find-Root $Root
switch ($Command) {
  'status' { Show-Status $r }
  'board'  { Show-Board $r }
  'doctor' { Show-Doctor $r }
}
