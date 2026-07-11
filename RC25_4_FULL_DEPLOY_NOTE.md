# TINO V12 RC25.4 Full Algorithm — Yahoo Restore

## Deployment
- Upload/overwrite the entire repository root with this package.
- Keep Streamlit Cloud Python version at **3.14**.
- Main module: `app.py`.

## Current market-data routing
- Price: existing Price Guard / TWSE MIS / Yahoo fallback.
- Institutional flow: Yahoo-first chip reader, FinMind fallback.
- Individual margin/short: Yahoo-first chip reader, FinMind fallback.
- Market heat: isolated direct Yahoo market-margin reader.
- Legacy GitHub market-heat updater is disabled.

## Safety
Market heat is isolated and must not clear or replace institutional-flow or individual margin/short data when Yahoo fails.
