# myCondition GitHub Sync Script
# Usage: .\sync_github.ps1

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$PROJECT_DIR = "E:\2026\myCondition"
$REPO_URL = "https://github.com/hoonoh57/myCondition.git"
$BRANCH = "main"

Set-Location $PROJECT_DIR
Write-Host "========================================"
Write-Host "  myCondition GitHub Sync"
Write-Host "  Path: $PROJECT_DIR"
Write-Host "========================================"

# 1) Check Git
try {
    $v = git --version
    Write-Host "[OK] $v" -ForegroundColor Green
}
catch {
    Write-Host "[ERROR] Git not installed" -ForegroundColor Red
    exit 1
}

# 2) Git UTF-8 config
git config --global core.quotepath false
git config --global i18n.commitEncoding utf-8
git config --global i18n.logOutputEncoding utf-8
git config --global gui.encoding utf-8
Write-Host "[OK] Git UTF-8 config done" -ForegroundColor Green

# 3) Check .gitignore
if (-not (Test-Path ".gitignore")) {
    Write-Host "[ERROR] .gitignore not found" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] .gitignore found" -ForegroundColor Green

# 4) Check .env protection
if (Test-Path ".env") {
    $check = Select-String -Path ".gitignore" -Pattern "^\.env" -Quiet
    if (-not $check) {
        Write-Host "[ERROR] .env not in .gitignore" -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] .env protected" -ForegroundColor Green
}

# 5) Init repo or verify
if (-not (Test-Path ".git")) {
    Write-Host "[INIT] Creating git repo..." -ForegroundColor Yellow
    git init
    git remote add origin $REPO_URL
    Write-Host "[OK] Remote set: $REPO_URL" -ForegroundColor Green
}
else {
    Write-Host "[OK] Git repo exists" -ForegroundColor Green
    $cur = git remote get-url origin 2>$null
    if ($cur -ne $REPO_URL) {
        git remote set-url origin $REPO_URL 2>$null
        if ($LASTEXITCODE -ne 0) { git remote add origin $REPO_URL }
    }
}

# 6) Stage all files
git add -A

# 7) Remove cached sensitive files
$patterns = @(".env", "*.log")
foreach ($p in $patterns) {
    $found = git ls-files --cached $p 2>$null
    if ($found) {
        Write-Host "[FIX] Removing cached: $p" -ForegroundColor Red
        git rm --cached $found 2>$null
    }
}

# 8) Show status
Write-Host ""
Write-Host "[STATUS] Files to commit:" -ForegroundColor Yellow
git status --short

# 9) Commit
$ts = Get-Date -Format "yyyy-MM-dd HH:mm"
$msg = "sync: $ts"
$st = git status --porcelain
if ($st) {
    git add -A
    git commit -m $msg
    Write-Host "[OK] Committed: $msg" -ForegroundColor Green
}
else {
    Write-Host "[INFO] No changes to commit" -ForegroundColor Gray
}

# 10) Set branch
$cb = git branch --show-current
if (-not $cb) {
    git branch -M $BRANCH
}

# 11) Push
Write-Host ""
Write-Host "[PUSH] Uploading to GitHub..." -ForegroundColor Yellow
git push -u origin $BRANCH 2>&1 | Write-Host

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "========================================"  -ForegroundColor Green
    Write-Host "  Sync complete!"                          -ForegroundColor Green
    Write-Host "  https://github.com/hoonoh57/myCondition" -ForegroundColor Green
    Write-Host "========================================"  -ForegroundColor Green
}
else {
    Write-Host ""
    Write-Host "[WARN] Push failed. Try:" -ForegroundColor Red
    Write-Host "  Option 1: git pull origin $BRANCH --rebase" -ForegroundColor Yellow
    Write-Host "            git push -u origin $BRANCH" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Option 2: git push -u origin $BRANCH --force" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Option 3 (auth): Create Personal Access Token at" -ForegroundColor Yellow
    Write-Host "    GitHub > Settings > Developer settings > Tokens" -ForegroundColor Yellow
}
