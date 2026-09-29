@echo off
setlocal enabledelayedexpansion

REM ============================================================================
REM  clean_stale_builds.cmd   --   NLawer stale build-artifact cleaner
REM ----------------------------------------------------------------------------
REM  Deletes ONLY regenerable build artifacts, and ONLY the exact ones listed
REM  below. Anything it finds that is NOT on the list is reported but NOT deleted.
REM  It can never touch the live ".next" folders that `next dev` / `next build` use.
REM
REM  USAGE
REM    Double-click it and answer the prompt.  (Easiest.)
REM
REM    Or from a command prompt in the NLawer folder:
REM      clean_stale_builds.cmd            -> DRY RUN + menu. Deletes NOTHING by itself.
REM      clean_stale_builds.cmd run        -> delete GROUP A, no menu
REM      clean_stale_builds.cmd run all    -> delete GROUP A + GROUP B, no menu
REM
REM  ---------------------------------------------------------------- GROUP A ---
REM  (8 dirs, ~1048 MB)  ".next_*" inside frontend\apps\*
REM  These are abandoned copies of previous builds. Pure garbage.
REM      frontend\apps\admin\.next_corrupt_devmix        124 MB
REM      frontend\apps\admin\.next_old_v14_keep2         152 MB
REM      frontend\apps\im\.next_old_v14_keep2            114 MB
REM      frontend\apps\lawyer\.next_old_v14_keep         162 MB
REM      frontend\apps\lawyer\.next_old_v14_keep2         83 MB
REM      frontend\apps\web\.next_eprerm_bak_115619       128 MB
REM      frontend\apps\web\.next_old_v14_keep            191 MB
REM      frontend\apps\web\.next_old_v14_keep2            94 MB
REM
REM  ---------------------------------------------------------------- GROUP B ---
REM  (3 dirs, ~2297 MB)  NOT what you originally asked for. Opt in with "ALL".
REM  Read these descriptions before choosing ALL -- one is NOT dead weight.
REM
REM    frontend\_prev_build                    1076 MB  [genuinely stale]
REM        Archived copies of older dev/prod builds (admin-dev-next-*, im-prod-
REM        build-*, ...). Nothing references it. Safe.
REM
REM    backend\_tmp_tests                      1204 MB  [ACTIVE scratch, not stale]
REM        32 test files write their throwaway SQLite DBs here. They call
REM        base.mkdir(exist_ok=True), so deleting it is SAFE -- the suite just
REM        recreates it -- but it WILL refill on the next test run. You are
REM        reclaiming 1.2 GB now, not freeing it permanently.
REM
REM    backend\_tmp_verify                       17 MB  [genuinely stale]
REM        One-off probe DBs and captured logs (authz_probe_*.db, backend8001.*).
REM
REM  ---------------------------------------------------------------- GROUP C ---
REM  (4 dirs, ~505 MB)  LIVE ".next" -- NEVER deleted by this script.
REM      frontend\apps\{admin,im,lawyer,web}\.next
REM
REM  Measured 2026-09-21. All targets verified as PLAIN DIRECTORIES, not
REM  junctions/symlinks (checked with Python os.lstat st_file_attributes).
REM  ============================================================================

set "ROOT=%~dp0"
set "MODE=%~1"
set "SCOPE=%~2"
set "LOG=%TEMP%\clean_stale_builds.log"

REM ---- sanity: are we at the project root? --------------------------------
if not exist "%ROOT%frontend\apps" (
    echo.
    echo [FATAL] Not a project root: "%ROOT%"
    echo         Expected to find "%ROOT%frontend\apps"
    echo         Put this .cmd file in the NLawer folder and run it again.
    echo.
    pause
    exit /b 1
)

> "%LOG%" echo clean_stale_builds.cmd  root=%ROOT%  mode=%MODE% %SCOPE%

echo.
echo ============================================================================
echo   NLawer stale build-artifact cleaner
echo   root : %ROOT%
echo ============================================================================
echo.

REM ==========================================================================
REM  DRY RUN  (always runs first, even when deleting)
REM ==========================================================================
echo -- GROUP A : ".next_*" under frontend\apps\*  ~1048 MB -------------------
echo.

set /a GA_N=0
set /a GA_UNKNOWN=0
for /d %%A in ("%ROOT%frontend\apps\*") do (
    for /d %%D in ("%%~A\.next_*") do (
        set "KNOWN=0"
        for %%K in (.next_old_v14_keep .next_old_v14_keep2 .next_corrupt_devmix .next_eprerm_bak_115619) do (
            if /i "%%~nxD"=="%%K" set "KNOWN=1"
        )
        if "!KNOWN!"=="1" (
            set /a GA_N+=1
            echo    [known]  frontend\apps\%%~nxA\%%~nxD
            >> "%LOG%" echo A known frontend\apps\%%~nxA\%%~nxD
        ) else (
            set /a GA_UNKNOWN+=1
            echo    [NEW ?]  frontend\apps\%%~nxA\%%~nxD
            >> "%LOG%" echo A UNKNOWN frontend\apps\%%~nxA\%%~nxD
        )
    )
)
echo.
echo    GROUP A: !GA_N! known + !GA_UNKNOWN! unknown
if !GA_UNKNOWN! GTR 0 (
    echo    *** WARNING: the "NEW ?" entries are NOT on the verified list.     ***
    echo    *** They will NOT be deleted. Read them before doing anything.      ***
)
echo.

echo -- GROUP B : other reclaimable dirs  ~2297 MB  -- needs ALL -------------
echo      frontend\_prev_build   = genuinely stale
echo      backend\_tmp_tests     = live test scratch, safe but it refills
echo      backend\_tmp_verify    = genuinely stale
echo.
for %%P in (
    "frontend\_prev_build"
    "backend\_tmp_tests"
    "backend\_tmp_verify"
) do (
    if exist "%ROOT%%%~P" (
        echo    [known]  %%~P
        >> "%LOG%" echo B known %%~P
    ) else (
        echo    [gone ]  %%~P
    )
)
echo.

echo -- GROUP C : LIVE ".next"  ~505 MB  -- NEVER TOUCHED ---------------------
echo.
for /d %%A in ("%ROOT%frontend\apps\*") do (
    if exist "%%~A\.next" echo    [KEEP ]  frontend\apps\%%~nxA\.next
)
echo.

REM ==========================================================================
REM  PICK A MODE
REM  If launched with "run", skip the menu. Otherwise ask, so that a plain
REM  double-click can actually get past the dry run.
REM ==========================================================================
if /i "%MODE%"=="run" goto :do_delete

echo ============================================================================
echo   Nothing has been deleted yet. What now?
echo.
echo       DELETE   -  delete GROUP A only          ~1048 MB
echo       ALL      -  delete GROUP A + GROUP B     ~3345 MB
echo       Enter    -  quit, delete nothing
echo ============================================================================
set "CONFIRM="
set /p "CONFIRM=  enter: "

if /i "!CONFIRM!"=="ALL" goto :choose_all
if /i "!CONFIRM!"=="DELETE" goto :choose_a

echo.
echo   Nothing was deleted.
echo.
pause
exit /b 0

:choose_all
set "SCOPE=all"
goto :do_delete

:choose_a
set "SCOPE="
goto :do_delete

REM ==========================================================================
REM  DELETE
REM ==========================================================================
:do_delete
echo.
set /a DEL_OK=0
set /a DEL_FAIL=0

echo -- deleting GROUP A --------------------------------------------------------
for /d %%A in ("%ROOT%frontend\apps\*") do (
    for /d %%D in ("%%~A\.next_*") do (
        set "KNOWN=0"
        for %%K in (.next_old_v14_keep .next_old_v14_keep2 .next_corrupt_devmix .next_eprerm_bak_115619) do (
            if /i "%%~nxD"=="%%K" set "KNOWN=1"
        )
        if "!KNOWN!"=="1" (
            call :rmtree "%%~fD"
        ) else (
            echo    [SKIP ]  not on the verified list: frontend\apps\%%~nxA\%%~nxD
            >> "%LOG%" echo SKIP-UNKNOWN frontend\apps\%%~nxA\%%~nxD
        )
    )
)
echo.

REM ---- GROUP B ------------------------------------------------------------
if /i "%SCOPE%"=="all" (
    echo -- deleting GROUP B ----------------------------------------------------
    call :rmtree "%ROOT%frontend\_prev_build"
    call :rmtree "%ROOT%backend\_tmp_tests"
    call :rmtree "%ROOT%backend\_tmp_verify"
    echo.
) else (
    echo -- GROUP B skipped, no "all" given. ----------------------------------
    echo.
)

REM ==========================================================================
REM  VERIFY
REM ==========================================================================
echo ============================================================================
echo   RESULT
echo ============================================================================
echo   removed : !DEL_OK!
echo   failed  : !DEL_FAIL!
echo.

echo   remaining GROUP A entries:
set /a LEFT=0
for /d %%A in ("%ROOT%frontend\apps\*") do (
    for /d %%D in ("%%~A\.next_*") do (
        echo      STILL THERE: frontend\apps\%%~nxA\%%~nxD
        set /a LEFT+=1
    )
)
if !LEFT! EQU 0 echo      none - GROUP A is clean
echo.

echo   live ".next" folders (must all still exist):
for /d %%A in ("%ROOT%frontend\apps\*") do (
    if exist "%%~A\.next" (
        echo      OK        frontend\apps\%%~nxA\.next
    ) else (
        echo      *** MISSING ***  frontend\apps\%%~nxA\.next
    )
)
echo.
echo   log: %LOG%
echo ============================================================================
echo.
pause
exit /b 0

REM ==========================================================================
REM  SUBROUTINE : rmtree <full-path>
REM  Refuses to delete a live ".next". Falls back to the robocopy trick for
REM  paths that exceed MAX_PATH (deep .next trees hit this often).
REM ==========================================================================
:rmtree
if not exist "%~1" (
    echo    [gone ]  %~1
    exit /b 0
)
if /i "%~nx1"==".next" (
    echo    [SKIP ]  refusing to delete LIVE .next : %~1
    >> "%LOG%" echo SKIP-LIVE %~1
    exit /b 0
)
rmdir /s /q "%~1" 2>nul
if not exist "%~1" (
    echo    [OK   ]  %~1
    >> "%LOG%" echo OK %~1
    set /a DEL_OK+=1
    exit /b 0
)
REM fallback: mirror an empty dir onto the target, then remove the husk
if not exist "%TEMP%\__empty_dir" mkdir "%TEMP%\__empty_dir" 2>nul
robocopy "%TEMP%\__empty_dir" "%~1" /MIR /NFL /NDL /NJH /NJS /NC /NS /NP >nul 2>&1
rmdir /s /q "%~1" 2>nul
if not exist "%~1" (
    echo    [OK   ]  %~1   -- via robocopy fallback
    >> "%LOG%" echo OK-ROBO %~1
    set /a DEL_OK+=1
    exit /b 0
)
echo    [FAIL ]  could not remove: %~1
>> "%LOG%" echo FAIL %~1
set /a DEL_FAIL+=1
exit /b 1
