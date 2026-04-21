# Albert Heijn Integration for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A custom Home Assistant integration for Albert Heijn (Dutch supermarket), providing receipt and savings data as sensors.

## Features

- **Last receipt total** — Amount of your most recent purchase
- **Last receipt discount** — Discount applied on the last receipt
- **Last receipt date** — Timestamp of the most recent receipt
- **Last receipt items** — Full item list of the most recent receipt as text
- **Receipt count** — Number of receipts this month
- **Total bonus savings** — Cumulative bonus/discount savings across all receipts

## Installation via HACS

1. In HACS, go to **Integrations** → click the three-dot menu → **Custom repositories**
2. Add `https://github.com/jesterret/ah-integration` and select category **Integration**
3. Install **Albert Heijn** from HACS
4. Restart Home Assistant

## Manual Installation

Copy the `custom_components/ah_integration` folder to your HA `config/custom_components/` directory and restart.

## Setup

1. Go to **Settings → Devices & Services → Add Integration** and search for **Albert Heijn**
2. Open the login URL shown in your browser and complete the Albert Heijn login
3. Copy the redirect URL (starts with `appie://`) or just the `code` from it and paste it into the form

### Getting the auth code locally

Albert Heijn currently redirects to the native `appie://` callback used by the mobile app, so Home Assistant cannot automatically complete the login handoff yet. For now, the integration expects you to paste either the full redirect URL or just the `code` value.

If copying the redirect URL is awkward on your device, run this locally on a machine with a browser:

```bash
uvx --from python-appie appie-login
```

That helper will guide you through the login flow and return either the authorization code or the full redirect URL. You can paste either form into the Home Assistant config flow.

Authentication tokens are stored in your HA config entry and auto-refreshed — you only need to log in once.

## Issues

Report bugs or request features at `https://github.com/jesterret/ah-integration/issues`.

## Requirements

- Home Assistant 2024.1+
- Python 3.11+
- An Albert Heijn account with the Appie app
