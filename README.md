# Chintamani Gems - Inventory Studio

Windows tray application with independent Natural and Lab Grown Excel profiles. The configured endpoint is `https://connect.drc.diamonds/v1/inventory/push`, matching the supplied curl example. All user-supplied import header aliases are supported. See [DRC mapping details](docs/DRC_API_MAPPING.md).

## Install and run

Use Python 3.12 or newer on Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python run.py
```

Close the window to keep monitoring in the tray. Use the tray Exit command to stop. A local lock prevents two running instances. State lives in `%LOCALAPPDATA%\DRCInventorySync\sync.db`.

## Configure the two Excel files

1. Open **Excel Mapping**. Under **Natural Diamonds**, choose **Connect / replace Excel**, select the worksheet and review the suggested column mappings. Repeat under **Lab Grown Diamonds** for its separate workbook. Each type has one connected file; replacements retain history and the existing contract settings.
2. Use **Edit column mapping** to adjust a connected workbook. Leave unused target fields blank. The inventory type is fixed by the selected connection and determines the outgoing Diamond Type and credential slot.
3. The customer installer automatically provisions the shared Natural and Lab Grown keys into Windows Credential Manager. No key entry is required. Replacing a workbook preserves credential setup; shared keys are managed by installer releases.
4. Open **Dashboard** to see results, last successful import, last failure and last sent upload. Filter history and select **View selected response**, or double-click a history row. DRC's successful queue acknowledgement displays **Sent to DRC** and triggers a Windows notification. It confirms delivery for processing, rather than a completed import.
5. Saving a connected Excel file sends a fresh snapshot after a stable interval, including unchanged inventory values. The app starts in the tray at Windows sign-in and resumes monitoring saved connections. Opening the app establishes the initial file state without uploading. Close keeps monitoring in the tray; Pause and Exit stop monitoring. Unknown results remain Needs review, and save-triggered requests never retry automatically.

No safe API connection test is documented; the application does not submit test inventory. No production endpoint was called during development. Do not call this integration production-ready until DRC confirms its contract and a real end-to-end validation succeeds.

The supplied import field list includes Diamond Type. Each preset profile now sends this accepted header with NATURAL or LAB_GROWN, as listed in the supplied database schema. Conflicting Excel type values are rejected. Confirm remote replacement/deletion semantics before enabling uploads.

## Change Rates

Open **Change Rates** in the sidebar. Under **DRC**, select **-** for a discount or **+** for an increase, enter a percentage and select **Save DRC rate**. The selected button indicates whether the percentage reduces or increases the rate. One DRC setting applies to Natural and Lab Grown uploads and persists across restarts.

Adjustments apply only to **Price Per Carat**, calculated from the original Excel value on every new snapshot and rounded to two decimal places using half-up rounding. A zero percentage preserves the original price. Excel files, the Discount field and Final Price stay unchanged. Discounts must be below 100%; adjusted prices must remain positive. Saving the rate does not send inventory; it applies on the next workbook save. Other website settings are stored independently when additional integrations are introduced.

## Behavior and limitations

- Supports `.xlsx` workbooks with a header in row one. Lab Grown mapped formula cells use the calculated results saved by Excel. Recalculate and save in Excel first; missing results or Excel errors block uploading with row/column details. The app does not calculate Excel formulas itself. Natural formula cells still require exported values. Preview shows the first ten rows. Required fields, positive numeric types and stock uniqueness are configurable.
- Watchdog monitors parent directories, including atomic replacement events. Periodic file-state scans recover missed events, and a two-second stable interval debounces saves. Parsing verifies that the file did not change during reading. Locked files are retried automatically on subsequent scans.
- Each job sends a complete `diamonds` array in one POST with `x-api-key`, HTTPS verification and no redirects. CSV and batching are intentionally unavailable until their semantics are verified. An empty inventory is blocked to avoid unintended deletion.
- Save-triggered profiles submit each distinct file save, including identical inventory values. Other profiles use normalized content hashes to skip duplicate payloads. Pending older snapshots are superseded per profile. Use only one profile for each remote inventory feed: two profiles targeting the same feed could replace each other's stock.
- Timeout/server failures are ambiguous, so default to review. Documented safe retries use exponential backoff; rate limits honor numeric Retry-After seconds. Credentials and malformed responses never trigger blind retries. Interrupted in-flight requests become review jobs after restart. Pending/retry jobs persist across restart.
- A success rule must prove complete acceptance. Common partial/failure fields override it, but an unknown DRC response shape must be addressed in the adapter before production. A generic HTTP 200 is never sufficient.
- Successful and superseded payloads are removed. Pending, failed and review jobs retain inventory in local SQLite to support retries; protect the Windows account and its local application data. Response bodies are redacted, bounded to 256 KB and saved locally for the response viewer; generic exception summaries avoid exposing credentials. See [response viewing](docs/RESPONSES.md).
- Configuration uses the Excel Mapping screen; the stored profile format remains compatible with previous releases. API keys remain in Windows Credential Manager.
- Close-to-tray requires a Windows notification area. Optional sign-in launch is provided by the installer. History shows up to the latest 300 attempts; workbook validation errors appear on the dashboard and in history.

## Architecture

`drc_sync/core.py`: SQLite queue, workbook validation/normalization, and configurable HTTP adapter. `drc_sync/app.py`: Qt UI, tray lifecycle, watchdog and serialized worker. The worker owns its SQLite connection; the UI owns a separate connection. Requests and file parsing run off the UI thread. Credentials are resolved by inventory type only when sending.

## Tests and Windows packaging

```powershell
python -m pytest -q
powershell -ExecutionPolicy Bypass -File packaging/build.ps1
```

The executable is `dist-next\ChintamaniGems\ChintamaniGems.exe`; distribute the entire folder. Optionally compile `packaging\installer.iss` with Inno Setup 6. The installer offers sign-in launch. Build on a Python 3.12+ Windows environment for the supported release. No Python installation is needed on the recipient's machine. Signing and installer compilation require your own Windows signing certificate and Inno Setup installation.


Reopening behavior: launching the executable again signals the existing app to restore its window, while keeping a single background worker. Single-click or double-click the Chintamani Gems tray icon to open it as well.



Chintamani Gems branding uses an original faceted gemstone mark in SVG, PNG and multi-resolution ICO formats under `drc_sync/assets`. The dashboard, Excel connections, rate table and History page use a light blue and white theme. The internal data directory, credential service and single-instance identity retain their existing names to preserve all saved connections and history. Only ChintamaniGems.exe is delivered; obsolete launcher names are removed by setup.

Sidebar navigation includes Dashboard, Excel Mapping, Change Rates and History. Collapse or expand the sidebar; its state persists across restarts. Activity details, result filters and response viewing are on History. Change Rates lists supported websites as independent rows. Increases are limited to 100%, discounts to 99.99%. Legacy invalid saved percentages remain intact and require an explicit valid correction before uploads; the app does not silently clamp them. The rate table shows only the website, sign controls, percentage, saved status and Save action.

Popups use consistent white layouts with secondary Cancel actions. The column-mapping dialog uses taller rows and explicit Skip column placeholders. Data tables use grey headers, fine grid borders and result badges; sidebar navigation uses matching outline icons and a simplified circular gem mark.

## Automatic updates — version 0.6.0

The delivery installer is `release/ChintamaniGemsSetup-0.6.3.exe`.
Install it once to add automatic release checks and verified downloads.
Future updates appear in the sidebar as **Install update**. Installation waits
for inventory processing to stop safely and requires **Install and restart**.
See [UPDATES.md](UPDATES.md) for release publishing and signing-key backup details.

Delivery build is 0.6.3. Final real Windows handoff testing checks the parent exit,
.NET SHA-256 verification and installer arguments without PowerShell module dependencies.

The customer installer now provisions the shared Natural and Lab Grown keys
automatically into Windows Credential Manager. No key entry or readable key file
is needed. See MANAGED_CREDENTIALS.md for provisioning and its security limits.
